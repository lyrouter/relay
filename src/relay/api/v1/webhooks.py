"""API-4 · webhook endpoint management (§8.3's ``/webhooks``, §8.5's semantics).

**Admin-only, and not by convention.** ``WEBHOOK_MANAGE`` is a capability no token
*scope* grants (``effective_capabilities``), so a service token cannot register a
destination however it was created — only a personal token held by an Admin can.
That asymmetry is the point: a machine principal able to add its own outbound
destination could copy every ticket in the tenant out of it without anybody
approving anything.

**The secret is shown once**, like an API token, because it is one. It is derived
rather than stored (``relay.app.webhooks.secret_for``), so there is no "show it
again" endpoint to build — the database genuinely does not have it. Losing it means
rotating, which is one request.

The delivery queue is exposed read-only plus **replay**, which is what makes the
dead letter §8.5 promises actually useful: a consumer that was down for a day gets
its events back rather than being told they were dropped.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from relay.api.problems import DEFAULT_ERROR_RESPONSES
from relay.api.v1.dependencies import AnyToken
from relay.app.webhooks import EVENT_TYPES, WebhookService
from relay.domain.enums import WebhookDeliveryState, WebhookState

router = APIRouter(
    prefix="/api/v1/webhooks",
    tags=["webhooks (v1)"],
    # §8.6 · the error shape is part of the contract, so it is in the
    # document rather than something an integrator discovers by failing.
    responses=DEFAULT_ERROR_RESPONSES,
)


class EndpointResponse(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    state: WebhookState
    created_at: dt.datetime | None


class RegisteredResponse(BaseModel):
    endpoint: EndpointResponse
    #: **Shown once.** Store it: the signature cannot be verified without it, and
    #: the database does not hold it (see the module note).
    secret: str
    #: Spelled out in the response because an integrator reads this before they
    #: read any document.
    signature_header: str = "X-Relay-Signature: sha256=HMAC(secret, timestamp + '.' + body)"


class RegisterPayload(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    #: Subset of the four §8.5 events. Unknown values are refused rather than
    #: ignored: an endpoint silently subscribed to nothing is worse than an error.
    event_types: list[str] = Field(min_length=1)


class StatePayload(BaseModel):
    state: WebhookState


class DeliveryResponse(BaseModel):
    id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    attempt: int
    state: WebhookDeliveryState
    next_retry_at: dt.datetime | None
    last_error: str | None
    created_at: dt.datetime | None


class EventTypesResponse(BaseModel):
    event_types: list[str]


def _endpoint(view) -> EndpointResponse:
    return EndpointResponse(
        id=view.id,
        url=view.url,
        event_types=list(view.event_types),
        state=view.state,
        created_at=view.created_at,
    )


def _delivery(view) -> DeliveryResponse:
    return DeliveryResponse(
        id=view.id,
        endpoint_id=view.endpoint_id,
        event_id=view.event_id,
        event_type=view.event_type,
        attempt=view.attempt,
        state=view.state,
        next_retry_at=view.next_retry_at,
        last_error=view.last_error,
        created_at=view.created_at,
    )


@router.get("/event-types", response_model=EventTypesResponse)
def event_types(token: AnyToken) -> EventTypesResponse:
    """The four events, so a subscriber does not have to hard-code strings."""
    return EventTypesResponse(event_types=list(EVENT_TYPES))


@router.post("", response_model=RegisteredResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterPayload, token: AnyToken) -> RegisteredResponse:
    """Register a destination. Refuses private, loopback and metadata targets (S-13).

    The refusal checks the **resolved** addresses as well as the literal host, so
    a name that resolves into private space is refused here rather than at the
    first delivery.
    """
    registered = WebhookService().register(payload.url, tuple(payload.event_types))
    return RegisteredResponse(
        endpoint=_endpoint(registered.endpoint), secret=registered.secret
    )


@router.get("", response_model=list[EndpointResponse])
def list_endpoints(token: AnyToken) -> list[EndpointResponse]:
    return [_endpoint(one) for one in WebhookService().list()]


@router.post("/{endpoint_id}/secret", response_model=RegisteredResponse)
def rotate(endpoint_id: uuid.UUID, token: AnyToken) -> RegisteredResponse:
    """Mint a new secret. **The old one stops working immediately** — there is no
    overlap window, because we can only send one signature per delivery. Rotate,
    then update the consumer."""
    rotated = WebhookService().rotate_secret(endpoint_id)
    return RegisteredResponse(endpoint=_endpoint(rotated.endpoint), secret=rotated.secret)


@router.put("/{endpoint_id}/state", response_model=EndpointResponse)
def set_state(endpoint_id: uuid.UUID, payload: StatePayload, token: AnyToken) -> EndpointResponse:
    """Pause or resume. Pausing **keeps** queued events, which is the whole
    difference from deleting: a consumer being redeployed should not lose the
    events that arrive during the window."""
    return _endpoint(WebhookService().set_state(endpoint_id, payload.state))


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(endpoint_id: uuid.UUID, token: AnyToken) -> None:
    WebhookService().delete(endpoint_id)


@router.get("/deliveries", response_model=list[DeliveryResponse])
def deliveries(
    token: AnyToken,
    state: WebhookDeliveryState | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DeliveryResponse]:
    """The queue: success rate, retries and the dead letter (§8.5's observability).

    Filter by ``state=dead_letter`` to find what needs replaying.
    """
    return [_delivery(one) for one in WebhookService().deliveries(state=state, limit=limit)]


@router.post("/deliveries/{delivery_id}/replay", response_model=DeliveryResponse)
def replay(delivery_id: uuid.UUID, token: AnyToken) -> DeliveryResponse:
    """Put a dead letter back on the queue with its attempt counter reset."""
    return _delivery(WebhookService().replay(delivery_id))
