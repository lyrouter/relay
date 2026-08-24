"""API-3 · ``Idempotency-Key`` (design §8.4).

**Why this exists even though ``external_ref`` also dedupes.** They defend against
different failures and §8.4 says both are required:

* this one defends against the **network** — a POST whose response was lost, a
  proxy retry, a client library's automatic retry. The same key replayed inside
  24 hours returns the first result, byte for byte;
* ``external_ref`` defends against the **upstream** — a user clicking submit three
  times, an alert firing twice, a compensating job re-running. It is a business
  fact stored in its own table with a unique constraint.

Neither substitutes for the other. A retry of a *different* submission carries a
new key and would slip past the first; two submissions of the *same* feedback
carry different keys and would slip past the second.

**A different body under the same key is a 422, not a replay.** Returning the
first response for a second, different request would be silent data loss — the
caller would be told their create succeeded while nothing of what they sent was
stored. So the fingerprint covers method, path and body, and a mismatch is
refused loudly.

**The record is written before the work, and completed after.** That order is what
makes two concurrent requests with one key safe: the unique constraint on
``(tenant_id, principal_id, idempotency_key)`` means the second one loses the
insert, and a loser whose winner has not finished yet is told to retry (409)
rather than being handed an empty result. The alternative — write the record
afterwards — leaves the window where both requests do the work, which is the
whole failure this is here to prevent.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from relay.app.errors import Conflict, ValidationFailed
from relay.context import current_context
from relay.infra.db.models import ApiIdempotencyRecord
from relay.infra.db.session import tenant_session

#: §8.4's window. Long enough to cover any retry a client or proxy performs,
#: short enough that the table is bounded without a purge job in S1.
RETENTION = dt.timedelta(hours=24)

KEY_REUSED = (
    "同一个 Idempotency-Key 用在了不同的请求上。换一个 key，或者原样重试上一次的请求。"
)
IN_FLIGHT = "同一个 Idempotency-Key 的请求正在处理中，请稍后用同样的 key 重试。"
KEY_TOO_LONG = "Idempotency-Key 过长。"

MAX_KEY_LENGTH = 255


@dataclass(frozen=True, slots=True)
class Replay:
    """A previously completed response, to be returned verbatim."""

    status: int
    body: dict[str, Any]


def fingerprint(method: str, path: str, body: Any) -> str:
    """Hash of what was asked, so a replay can be told from a reused key.

    ``sort_keys`` because two JSON objects that differ only in key order are the
    same request, and a client library is free to serialise them differently
    between the first attempt and the retry.
    """
    payload = json.dumps(body, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(f"{method.upper()} {path}\n{payload}".encode()).hexdigest()


def begin(
    key: str,
    method: str,
    path: str,
    body: Any,
    *,
    now: dt.datetime | None = None,
) -> Replay | None:
    """Claim ``key`` for this request, or return the response it already produced.

    Three outcomes:

    * **None** — the key is ours; do the work and call :func:`complete`.
    * a :class:`Replay` — the same request already succeeded; return it unchanged.
    * a raise — either the key was used for a *different* request
      (:class:`ValidationFailed`, 422) or an identical one is still in flight
      (:class:`Conflict`, 409, safe to retry).

    ``principal_id`` comes from the context, so one tenant's keys cannot collide
    with another's and one integration's cannot collide with a colleague's. A
    service token has no user, so its token-less contexts key on the tenant's own
    id — which is correct: a machine principal *is* the caller there.
    """
    now = now or dt.datetime.now(dt.UTC)
    if len(key) > MAX_KEY_LENGTH:
        raise ValidationFailed(KEY_TOO_LONG)
    ctx = current_context()
    principal_id = ctx.actor_id or ctx.tenant_id
    digest = fingerprint(method, path, body)

    with tenant_session() as session:
        existing = session.scalars(
            select(ApiIdempotencyRecord).where(
                ApiIdempotencyRecord.principal_id == principal_id,
                ApiIdempotencyRecord.idempotency_key == key,
            )
        ).first()

        if existing is not None and existing.expires_at <= now:
            # Expired: the key is free again. Deleting rather than reusing the row
            # keeps ``begin`` a single code path — claim or replay, never "claim
            # by mutating somebody else's record".
            session.delete(existing)
            session.flush()
            existing = None

        if existing is not None:
            if existing.request_fingerprint != digest:
                raise ValidationFailed(KEY_REUSED)
            if existing.response_status is None:
                raise Conflict(IN_FLIGHT)
            return Replay(
                status=existing.response_status, body=dict(existing.response_snapshot or {})
            )

        session.add(
            ApiIdempotencyRecord(
                tenant_id=ctx.tenant_id,
                principal_id=principal_id,
                idempotency_key=key,
                request_fingerprint=digest,
                expires_at=now + RETENTION,
            )
        )
        try:
            session.commit()
        except IntegrityError as exc:
            # Lost the race for the key. The winner is mid-flight, so the honest
            # answer is "retry", not "here is an empty result".
            session.rollback()
            raise Conflict(IN_FLIGHT) from exc
        return None


def complete(key: str, status: int, body: dict[str, Any]) -> None:
    """Record the response so a replay can return it.

    Committed separately from the work it describes, and that is a deliberate
    trade rather than an oversight: sharing the transaction would mean a ticket
    that was created but whose idempotency record rolled back — turning one
    retry into a second ticket. This way the failure mode is the harmless one
    (the record exists, the caller retries, and gets the replay).
    """
    ctx = current_context()
    principal_id = ctx.actor_id or ctx.tenant_id
    with tenant_session() as session:
        record = session.scalars(
            select(ApiIdempotencyRecord).where(
                ApiIdempotencyRecord.principal_id == principal_id,
                ApiIdempotencyRecord.idempotency_key == key,
            )
        ).first()
        if record is None:  # pragma: no cover - only if the row was purged mid-request
            return
        record.response_status = status
        record.response_snapshot = body
        session.commit()


def abandon(key: str) -> None:
    """Release a claim whose request failed.

    Without this, a create that failed with a 422 would leave its key claimed and
    the client's corrected retry — same key, fixed body — would be refused as a
    reused key. The client did nothing wrong, so the key goes back.
    """
    ctx = current_context()
    principal_id = ctx.actor_id or ctx.tenant_id
    with tenant_session() as session:
        record = session.scalars(
            select(ApiIdempotencyRecord).where(
                ApiIdempotencyRecord.principal_id == principal_id,
                ApiIdempotencyRecord.idempotency_key == key,
                ApiIdempotencyRecord.response_status.is_(None),
            )
        ).first()
        if record is not None:
            session.delete(record)
            session.commit()


def purge_expired(*, now: dt.datetime | None = None) -> int:
    """Delete this tenant's records past their 24 hours. Returns how many.

    Exists so the table has an answer to "who cleans this up?" — called from the
    same cron entry as the log-version purge. Nothing depends on it running: an
    expired record is already ignored (and deleted) by :func:`begin` the next time
    its key is presented.
    """
    now = now or dt.datetime.now(dt.UTC)
    with tenant_session() as session:
        rows = list(
            session.scalars(
                select(ApiIdempotencyRecord).where(ApiIdempotencyRecord.expires_at <= now)
            )
        )
        for row in rows:
            session.delete(row)
        session.commit()
        return len(rows)


def purge_expired_every_tenant(*, now: dt.datetime | None = None) -> dict[str, int]:
    """What cron calls. Returns ``{tenant_slug: records deleted}``.

    Same shape as the log-version purge, and for the same reasons: one tenant per
    transaction so a failure does not take the others down, the tenant *list* read
    through the audited ``SystemRepository`` with a written reason, and everything
    after that per-tenant under RLS exactly as a request would do it.
    """
    from relay.app.logs.retention import system_context
    from relay.context import tenant_scope
    from relay.infra.db.system_repository import SystemRepository

    counted: dict[str, int] = {}
    tenants = SystemRepository().list_tenants("scheduled idempotency-record sweep (API-3)")
    for tenant in tenants:
        with tenant_scope(system_context(tenant.id)):
            counted[tenant.slug] = purge_expired(now=now)
    return counted


def key_of(candidate: str | None) -> str | None:
    """Normalise the header. Blank is absent, not a key of empty string."""
    cleaned = (candidate or "").strip()
    return cleaned or None


def new_key() -> str:
    """A key for callers that want one generated (tests, scripts)."""
    return str(uuid.uuid4())
