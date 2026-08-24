"""API-5 · per-token rate limits (design §8.6, decided S-14).

**Loose on purpose.** 600 reads and 120 writes per minute per token, to be
tightened after two weeks of real traffic. Shipping tight limits would make the
first integration look like a broken API, and the first integration is the AI
Gateway feedback form — with a real person waiting for an answer at the other end
(§8.8). Full instrumentation now, narrowing later, was the decision.

**In PostgreSQL, not in memory**, for the reason ``models/throttle.py`` already
gives: a limit that resets when a worker restarts is not a limit, and S1
deliberately has no Redis. The cost is honest and worth naming — a *read* API call
performs one UPDATE, so this table is the busiest one on the API path. It stays
affordable because the row is keyed by token and touched once per request, and
because the window is fixed rather than sliding (one row, one UPDATE, no history).

**A fixed window, and no punishment period.** Unlike the signup limiter, going
over does not extend a block: a client that exceeds its quota gets 429 with the
seconds remaining in the window, and works again as soon as the window turns.
Blocking longer would mean one burst from a CI job silently pausing an alerting
integration that shares the token — and an integration that stops for fifteen
minutes without saying so is worse than one that is briefly told to slow down.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app.errors import RateLimited
from relay.infra.db.models import Throttle
from relay.infra.db.pre_tenant import PreTenantRepository

#: One minute, matching how the decided numbers are expressed (S-14). Anything
#: else would make "600 per minute" a translation rather than a setting.
WINDOW = dt.timedelta(minutes=1)

READ_BUCKET = "api_read"
WRITE_BUCKET = "api_write"

#: S-14's decided starting values. Deliberately generous; the review after two
#: weeks of real usage is the thing that narrows them.
READ_PER_MINUTE = 600
WRITE_PER_MINUTE = 120

TOO_MANY = "请求过于频繁，请稍后重试。"

#: Methods that count against the write quota. Same list the CSRF check uses, and
#: for the same reason: everything else is a read.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class Decision:
    """What the response should tell the caller about its quota.

    Returned even on success because §8.6 asks for ``X-RateLimit-*`` on every
    response, not only on the refusal — a client that can see its remaining
    budget can slow itself down, which is the whole point of instrumenting before
    tightening.
    """

    limit: int
    remaining: int
    #: Seconds until the window turns. Doubles as ``Retry-After`` on a 429.
    reset_after: int


def consume(
    token_id: uuid.UUID, method: str, *, now: dt.datetime | None = None
) -> Decision:
    """Count one request against ``token_id``; raise :class:`RateLimited` if over.

    Runs on the pre-tenant connection, like the signup limiter and for the same
    structural reason: ``throttle`` is the one table with no ``tenant_id``,
    because a refused request must still be counted and the tenant boundary is
    not what this is protecting.

    The row is locked for the read-modify-write, so two concurrent requests on
    one token cannot both see 599 and both pass.
    """
    now = now or dt.datetime.now(dt.UTC)
    write = method.upper() in WRITE_METHODS
    bucket = WRITE_BUCKET if write else READ_BUCKET
    ceiling = WRITE_PER_MINUTE if write else READ_PER_MINUTE
    key_hash = hashlib.sha256(str(token_id).encode("utf-8")).hexdigest()

    with PreTenantRepository().session() as session:
        row = session.scalars(
            select(Throttle)
            .where(Throttle.bucket == bucket, Throttle.key_hash == key_hash)
            .with_for_update()
        ).first()

        if row is None:
            session.add(
                Throttle(
                    bucket=bucket, key_hash=key_hash, attempts=1, window_started_at=now
                )
            )
            session.flush()
            return Decision(
                limit=ceiling, remaining=ceiling - 1, reset_after=int(WINDOW.total_seconds())
            )

        elapsed = now - row.window_started_at
        if elapsed >= WINDOW:
            row.window_started_at = now
            row.attempts = 1
            row.blocked_until = None
            session.flush()
            return Decision(
                limit=ceiling, remaining=ceiling - 1, reset_after=int(WINDOW.total_seconds())
            )

        remaining_window = max(1, int((WINDOW - elapsed).total_seconds()))
        row.attempts += 1
        session.flush()
        if row.attempts > ceiling:
            # Raising discards this increment (the pre-tenant session commits on
            # exit, and this exits by exception) — which is correct rather than a
            # leak: the stored count is already at the ceiling, so every further
            # request in this window recomputes the same refusal. Unlike the
            # signup limiter there is no ``blocked_until`` to persist, so there
            # is nothing that needs a ``commit_and_raise``.
            raise RateLimited(
                TOO_MANY,
                retry_after_seconds=remaining_window,
                # Surfaced in the problem document so a client debugging a 429
                # can see which quota it hit without reading our source.
                detail={
                    "limit": ceiling,
                    "window_seconds": int(WINDOW.total_seconds()),
                    "scope": "write" if write else "read",
                },
            )
        return Decision(
            limit=ceiling,
            remaining=max(0, ceiling - row.attempts),
            reset_after=remaining_window,
        )
