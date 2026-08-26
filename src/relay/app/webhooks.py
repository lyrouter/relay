"""API-4 · outbound webhooks: the queue, the signature, and the retries (§8.5).

Four events (`ticket.created` · `ticket.updated` · `ticket.status_changed` ·
`ticket.comment_created`), **at-least-once and unordered**. That is not a
weakness to apologise for, it is the contract: retries mean duplicates, and
parallel delivery means reordering. What makes it usable is what rides in the
payload — ``event_id`` so a consumer can dedupe, and ``rev`` so it can drop an
event older than what it already has (§8.4). Both are in the payload of every
event, and the integration guide tells consumers to use them.

**The queue is a PostgreSQL table drained with ``FOR UPDATE SKIP LOCKED``** (D-0).
No Redis, no message broker. This is S1's only real queue consumer and the same
facility Phase 2's GH sync dead-letter queue will use, so it is worth being plain
about the trade: PG gives transactional enqueue — an event cannot exist for a
ticket whose write rolled back, because both are in one transaction — and costs a
polling worker instead of a push. At this volume that is the right side of the
trade.

**Enqueue is transactional; delivery is not.** :func:`emit` adds rows to the
caller's session, so it is an outbox: the event and the change it describes commit
together or not at all. Sending happens later, in ``scripts/deliver_webhooks.py``.
The alternative — POST inside the request — makes a slow consumer into slow
tickets, and a failing consumer into failed writes.

**Secrets are derived, never stored** — see :func:`secret_for`. The database holds
no signing material at all, so a dump cannot be used to forge our signatures.

**Destinations are checked twice**: when the endpoint is registered and again
immediately before each delivery, against the **resolved** addresses (S-13). Once
is not enough — a hostname that was public when it was saved can point at
``169.254.169.254`` by the time we send, which is precisely the DNS-rebinding case
the decision names.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import socket
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from relay.app import audit
from relay.app.authz import actor_principal, require
from relay.app.errors import NotFound, ValidationFailed
from relay.config import settings
from relay.context import current_context
from relay.domain.destinations import literal_refusal, resolved_refusal, scheme_and_host
from relay.domain.enums import WebhookDeliveryState, WebhookState
from relay.domain.permissions import Capability
from relay.infra.db.models import WebhookDelivery, WebhookEndpoint
from relay.infra.db.session import tenant_session
from relay.infra.http import UrllibTransport
from relay.ports.webhook import WebhookTransport

logger = logging.getLogger("relay.webhooks")

#: §8.5. Closed on purpose: adding an event type is a contract change, so it
#: should be a line in a diff rather than a string literal at a call site.
TICKET_CREATED = "ticket.created"
TICKET_UPDATED = "ticket.updated"
TICKET_STATUS_CHANGED = "ticket.status_changed"
TICKET_COMMENT_CREATED = "ticket.comment_created"

EVENT_TYPES = (
    TICKET_CREATED,
    TICKET_UPDATED,
    TICKET_STATUS_CHANGED,
    TICKET_COMMENT_CREATED,
)

#: §8.5's schedule. Five attempts, then the dead letter — which is *kept and
#: replayable*, not discarded: a consumer that was down for a day needs its
#: events back, and "we dropped them" is the answer that ends an integration.
BACKOFF = (
    dt.timedelta(minutes=1),
    dt.timedelta(minutes=5),
    dt.timedelta(minutes=30),
    dt.timedelta(hours=2),
    dt.timedelta(hours=6),
)

MAX_ATTEMPTS = len(BACKOFF)

#: Long enough for a consumer doing real work, short enough that one hung
#: endpoint cannot hold a worker slot for a minute.
SEND_TIMEOUT_SECONDS = 10

UNKNOWN_EVENT = "未知的事件类型。"
ENDPOINT_NOT_FOUND = "找不到该 webhook 端点。"
NO_EVENTS = "至少要订阅一个事件类型。"


@dataclass(frozen=True, slots=True)
class EndpointView:
    id: uuid.UUID
    url: str
    event_types: tuple[str, ...]
    state: WebhookState
    created_by: uuid.UUID | None
    created_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class RegisteredEndpoint:
    """The endpoint plus its signing secret — shown **once**, like a token."""

    endpoint: EndpointView
    secret: str


@dataclass(frozen=True, slots=True)
class DeliveryView:
    id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    attempt: int
    state: WebhookDeliveryState
    next_retry_at: dt.datetime | None
    last_error: str | None
    created_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, ready to be fanned out to subscribers."""

    event_type: str
    ticket: dict[str, Any]
    changes: dict[str, Any] | None = None
    #: Only for comment events, so a consumer does not have to re-read the ticket.
    comment: dict[str, Any] | None = None


# ------------------------------------------------------------------- secrets


def secret_for(endpoint_id: uuid.UUID, version: int) -> str:
    """The signing secret for an endpoint, derived rather than stored.

    ``HMAC(master key, "<endpoint id>:<version>")``. Three consequences, all of
    them the ones we want:

    * **the database holds no signing material.** A dump of ``webhook_endpoint``
      cannot be used to forge a signature — unlike the obvious design, where the
      secret has to be stored recoverably because HMAC needs it back;
    * **rotation is a counter.** Incrementing ``secret_version`` invalidates the
      old secret and mints a new one with nothing to migrate;
    * **the master key is a real dependency.** Changing ``RELAY_WEBHOOK_SIGNING_KEY``
      silently changes every endpoint's secret, and every consumer's verification
      starts failing. ``secret_hash`` exists to *detect* exactly that (see
      :func:`_check_master_key`) instead of leaving it to be diagnosed from
      somebody else's logs.
    """
    return hmac.new(
        _master_key().encode("utf-8"),
        f"{endpoint_id}:{version}".encode(),
        hashlib.sha256,
    ).hexdigest()


def signature_of(secret: str, timestamp: str, body: bytes) -> str:
    """``sha256=HMAC(secret, timestamp + "." + body)`` (§8.5).

    The timestamp is *inside* the signed material, which is what makes the
    consumer's replay check meaningful: without it an attacker could re-send a
    captured body with a fresh timestamp header and the signature would still
    verify.
    """
    digest = hmac.new(
        secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def _master_key() -> str:
    # Reuses the blob signing key's discipline: one value per environment, in the
    # environment. ``check_configuration`` warns when it is still the default.
    return settings.webhook_signing_key


def _check_master_key(endpoint: WebhookEndpoint) -> str:
    """Return the endpoint's current secret, complaining if the key changed."""
    secret = secret_for(endpoint.id, endpoint.secret_version)
    expected = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    if endpoint.secret_hash and not hmac.compare_digest(endpoint.secret_hash, expected):
        logger.error(
            "webhook endpoint %s: the derived secret no longer matches the stored "
            "fingerprint. RELAY_WEBHOOK_SIGNING_KEY has changed, so every consumer's "
            "signature verification is now failing. Restore the previous key, or "
            "rotate each endpoint and hand out the new secrets.",
            endpoint.id,
        )
    return secret


# ------------------------------------------------------------- registration


class WebhookService:
    """Endpoint management. Admin-only, audited, and runs inside a tenant scope."""

    def register(
        self, url: str, event_types: tuple[str, ...], *, now: dt.datetime | None = None
    ) -> RegisteredEndpoint:
        """Register a destination and hand back its secret once.

        ``WEBHOOK_MANAGE`` is an Admin capability that **no token scope grants**
        (see ``effective_capabilities``), so a service token cannot register a
        webhook however it was created. That is the point: a machine principal
        that could add its own outbound destination could exfiltrate every ticket
        in the tenant without anyone approving a thing.
        """
        clean_url = (url or "").strip()
        wanted = tuple(dict.fromkeys(event_types))
        if not wanted:
            raise ValidationFailed(NO_EVENTS)
        for one in wanted:
            if one not in EVENT_TYPES:
                raise ValidationFailed(UNKNOWN_EVENT, detail={"event_type": one})
        refusal = literal_refusal(clean_url)
        if refusal:
            raise ValidationFailed(refusal)
        _refuse_unresolvable(clean_url)

        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.WEBHOOK_MANAGE)
            endpoint = WebhookEndpoint(
                tenant_id=actor.tenant_id,
                url=clean_url,
                secret_version=1,
                secret_hash="",
                event_types=list(wanted),
                state=WebhookState.ACTIVE,
                created_by=actor.user_id,
            )
            session.add(endpoint)
            session.flush()
            secret = secret_for(endpoint.id, endpoint.secret_version)
            endpoint.secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
            audit.record(
                session,
                "webhook.registered",
                target_type="webhook_endpoint",
                target_id=endpoint.id,
                after={"url": clean_url, "event_types": list(wanted)},
            )
            view = _endpoint_view(endpoint)
            session.commit()
            return RegisteredEndpoint(endpoint=view, secret=secret)

    def list(self) -> list[EndpointView]:
        with tenant_session() as session:
            require(actor_principal(session), Capability.WEBHOOK_MANAGE)
            return [
                _endpoint_view(row)
                for row in session.scalars(
                    select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc())
                )
            ]

    def rotate_secret(self, endpoint_id: uuid.UUID) -> RegisteredEndpoint:
        """Mint a new secret for an endpoint; the old one stops verifying at once.

        No overlap window, deliberately. An overlap would mean accepting both
        secrets for a while, which is a property of the *consumer's* verification,
        not of our signing — we can only send one signature. So rotation is a
        coordinated act: rotate, then update the consumer. The alternative
        (pretend there is a grace period) would be a comforting lie.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.WEBHOOK_MANAGE)
            endpoint = _load(session, endpoint_id)
            endpoint.secret_version += 1
            secret = secret_for(endpoint.id, endpoint.secret_version)
            endpoint.secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
            audit.record(
                session,
                "webhook.secret_rotated",
                target_type="webhook_endpoint",
                target_id=endpoint.id,
                after={"secret_version": endpoint.secret_version},
            )
            view = _endpoint_view(endpoint)
            session.commit()
            return RegisteredEndpoint(endpoint=view, secret=secret)

    def set_state(self, endpoint_id: uuid.UUID, state: WebhookState) -> EndpointView:
        """Pause or resume an endpoint.

        Pausing keeps queued deliveries: a consumer being redeployed should not
        lose the events that arrive during the window, which is the difference
        between pausing and deleting.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.WEBHOOK_MANAGE)
            endpoint = _load(session, endpoint_id)
            before = str(endpoint.state)
            endpoint.state = state
            audit.record(
                session,
                "webhook.state_changed",
                target_type="webhook_endpoint",
                target_id=endpoint.id,
                before={"state": before},
                after={"state": str(state)},
            )
            view = _endpoint_view(endpoint)
            session.commit()
            return view

    def delete(self, endpoint_id: uuid.UUID) -> None:
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.WEBHOOK_MANAGE)
            endpoint = _load(session, endpoint_id)
            audit.record(
                session,
                "webhook.deleted",
                target_type="webhook_endpoint",
                target_id=endpoint.id,
                before={"url": endpoint.url},
            )
            # Queued and dead-lettered rows go with it: they name an endpoint
            # that no longer exists, and replaying them has nowhere to go.
            for delivery in session.scalars(
                select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint.id)
            ):
                session.delete(delivery)
            session.delete(endpoint)
            session.commit()

    def deliveries(
        self, *, state: WebhookDeliveryState | None = None, limit: int = 100
    ) -> list[DeliveryView]:
        """The queue, for the observability §8.5 asks for and for replaying a
        dead letter."""
        with tenant_session() as session:
            require(actor_principal(session), Capability.WEBHOOK_MANAGE)
            query = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc())
            if state is not None:
                query = query.where(WebhookDelivery.state == state)
            return [_delivery_view(row) for row in session.scalars(query.limit(limit))]

    def replay(self, delivery_id: uuid.UUID, *, now: dt.datetime | None = None) -> DeliveryView:
        """Put a dead letter back on the queue, attempt counter reset."""
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.WEBHOOK_MANAGE)
            delivery = session.get(WebhookDelivery, delivery_id)
            if delivery is None:
                raise NotFound("找不到该投递记录。")
            delivery.state = WebhookDeliveryState.PENDING
            delivery.attempt = 0
            delivery.next_retry_at = now
            delivery.last_error = None
            audit.record(
                session,
                "webhook.replayed",
                target_type="webhook_delivery",
                target_id=delivery.id,
                after={"event_type": delivery.event_type},
            )
            view = _delivery_view(delivery)
            session.commit()
            return view


# ---------------------------------------------------------------- enqueueing


def emit(session, event: Event, *, now: dt.datetime | None = None) -> int:
    """Queue ``event`` for every active endpoint subscribed to it.

    Called **inside the use case's transaction** (the outbox property in the
    module note) and returns how many deliveries were queued, which is what a
    test asserts on.

    Failures here are swallowed and logged rather than raised, and that is a
    considered trade: a webhook subscriber must not be able to fail a ticket
    write. The cost is a lost event, and the mitigation is that this code path
    does nothing but INSERT rows the caller is already committing.
    """
    now = now or dt.datetime.now(dt.UTC)
    ctx = current_context()
    try:
        endpoints = list(
            session.scalars(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.state == WebhookState.ACTIVE,
                    WebhookEndpoint.event_types.any(event.event_type),
                )
            )
        )
        for endpoint in endpoints:
            session.add(
                WebhookDelivery(
                    tenant_id=ctx.tenant_id,
                    endpoint_id=endpoint.id,
                    event_id=uuid.uuid4(),
                    event_type=event.event_type,
                    payload=_payload(event, now=now),
                    attempt=0,
                    state=WebhookDeliveryState.PENDING,
                    next_retry_at=now,
                )
            )
        session.flush()
        return len(endpoints)
    except Exception:  # pragma: no cover - defensive; see the docstring
        logger.exception("could not queue webhook event %s", event.event_type)
        return 0


def _payload(event: Event, *, now: dt.datetime) -> dict[str, Any]:
    """§8.5's payload. ``event_id`` is added per delivery, at send time.

    ``actor`` carries ``actor_type`` because that is what Phase 2's GH loop guard
    filters on (§8.4): a consumer that writes back into Relay has to be able to
    recognise its own writes coming around again.
    """
    ctx = current_context()
    body: dict[str, Any] = {
        "event_type": event.event_type,
        "occurred_at": now.isoformat(),
        "tenant": {"id": str(ctx.tenant_id)},
        "actor": {
            "id": str(ctx.actor_id) if ctx.actor_id else None,
            "actor_type": str(ctx.actor_type),
            "origin": str(ctx.origin),
        },
        "ticket": event.ticket,
    }
    if event.changes is not None:
        body["changes"] = event.changes
    if event.comment is not None:
        body["comment"] = event.comment
    return body


def ticket_summary(view, tenant_slug: str | None = None) -> dict[str, Any]:
    """The ticket shape inside a payload — **including ``rev``** (§8.4).

    A subset rather than the whole ticket: a payload is a notification, not a
    replica, and a consumer that needs the current state should read the API (and
    then it gets the current state rather than a stale copy).
    """
    return {
        "id": str(view.id),
        "key": view.key,
        "number": view.number,
        "type": str(view.type),
        "title": view.title,
        "status": str(view.status),
        "priority": str(view.priority),
        "assignee_id": str(view.assignee_id) if view.assignee_id else None,
        "reporter_id": str(view.reporter_id) if view.reporter_id else None,
        "rev": view.rev,
        "source": view.source,
        "category": str(view.category) if view.category else None,
        "updated_at": view.updated_at.isoformat() if view.updated_at else None,
    }


# ----------------------------------------------------------------- delivery


class WebhookDispatcher:
    """Drains the queue. Runs from ``scripts/deliver_webhooks.py``, per tenant.

    ``FOR UPDATE SKIP LOCKED`` is what makes more than one worker safe: each
    claims different rows and neither waits on the other. Without SKIP LOCKED a
    second worker would block on the first's rows, which is a queue that scales to
    exactly one consumer.
    """

    def __init__(self, transport: WebhookTransport | None = None) -> None:
        self._transport = transport or UrllibTransport()

    def dispatch_batch(self, *, limit: int = 20, now: dt.datetime | None = None) -> dict[str, int]:
        """Deliver up to ``limit`` due rows. Returns a per-outcome count."""
        now = now or dt.datetime.now(dt.UTC)
        outcome = {"delivered": 0, "retrying": 0, "dead_letter": 0}

        with tenant_session() as session:
            claimed = list(
                session.scalars(
                    select(WebhookDelivery)
                    .where(
                        WebhookDelivery.state == WebhookDeliveryState.PENDING,
                        WebhookDelivery.next_retry_at <= now,
                    )
                    .order_by(WebhookDelivery.next_retry_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for delivery in claimed:
                delivery.state = WebhookDeliveryState.IN_FLIGHT
            session.commit()

            for delivery in claimed:
                result = self._attempt(session, delivery, now=now)
                outcome[result] += 1
            session.commit()
        return outcome

    def _attempt(self, session, delivery: WebhookDelivery, *, now: dt.datetime) -> str:
        endpoint = session.get(WebhookEndpoint, delivery.endpoint_id)
        if endpoint is None or endpoint.state is not WebhookState.ACTIVE:
            # Paused or deleted mid-flight: back to pending, to be picked up when
            # it resumes. Not a failed attempt — the consumer never saw it.
            delivery.state = WebhookDeliveryState.PENDING
            delivery.next_retry_at = now + BACKOFF[0]
            return "retrying"

        # The second destination check (S-13). The address is resolved *now*,
        # because a hostname that was public at registration can point into
        # private space by the time we deliver.
        refusal = literal_refusal(endpoint.url) or _resolution_refusal(endpoint.url)
        if refusal:
            return self._fail(delivery, f"destination refused: {refusal}", now=now, fatal=True)

        body = json.dumps(
            {"event_id": str(delivery.event_id), **delivery.payload},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        timestamp = str(int(now.timestamp()))
        secret = _check_master_key(endpoint)
        headers = {
            "Content-Type": "application/json",
            "X-Relay-Timestamp": timestamp,
            "X-Relay-Signature": signature_of(secret, timestamp, body),
            "X-Relay-Event": delivery.event_type,
            "X-Relay-Delivery": str(delivery.event_id),
            "User-Agent": "relay-webhooks/1",
        }

        try:
            code = self._transport.post(
                endpoint.url, body, headers, timeout=SEND_TIMEOUT_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - any transport failure is a retry
            return self._fail(delivery, f"{type(exc).__name__}: {exc}", now=now)

        if 200 <= code < 300:
            delivery.state = WebhookDeliveryState.DELIVERED
            delivery.attempt += 1
            delivery.next_retry_at = None
            delivery.last_error = None
            return "delivered"
        return self._fail(delivery, f"HTTP {code}", now=now)

    def _fail(
        self, delivery: WebhookDelivery, reason: str, *, now: dt.datetime, fatal: bool = False
    ) -> str:
        """Schedule the next attempt, or give up into the dead letter.

        ``fatal`` skips the retries for a failure that cannot improve — a refused
        destination will be refused identically in six hours, and five attempts
        at it is five chances for a rebinding attack rather than one.
        """
        delivery.attempt += 1
        delivery.last_error = reason[:2000]
        if fatal or delivery.attempt >= MAX_ATTEMPTS:
            delivery.state = WebhookDeliveryState.DEAD_LETTER
            delivery.next_retry_at = None
            return "dead_letter"
        delivery.state = WebhookDeliveryState.PENDING
        delivery.next_retry_at = now + BACKOFF[delivery.attempt]
        return "retrying"


# ---------------------------------------------------------------- internals


def _resolution_refusal(url: str) -> str | None:
    _, host, _ = scheme_and_host(url)
    return resolved_refusal(_resolve(host))


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def _refuse_unresolvable(url: str) -> None:
    refusal = _resolution_refusal(url)
    if refusal:
        raise ValidationFailed(refusal)


def _load(session, endpoint_id: uuid.UUID) -> WebhookEndpoint:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise NotFound(ENDPOINT_NOT_FOUND)
    return endpoint


def _endpoint_view(endpoint: WebhookEndpoint) -> EndpointView:
    return EndpointView(
        id=endpoint.id,
        url=endpoint.url,
        event_types=tuple(endpoint.event_types or ()),
        state=endpoint.state,
        created_by=endpoint.created_by,
        created_at=endpoint.created_at,
    )


def _delivery_view(delivery: WebhookDelivery) -> DeliveryView:
    return DeliveryView(
        id=delivery.id,
        endpoint_id=delivery.endpoint_id,
        event_id=delivery.event_id,
        event_type=delivery.event_type,
        attempt=delivery.attempt,
        state=delivery.state,
        next_retry_at=delivery.next_retry_at,
        last_error=delivery.last_error,
        created_at=delivery.created_at,
    )
