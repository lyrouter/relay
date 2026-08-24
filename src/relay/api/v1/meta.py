"""API-2 · ``/meta/*`` — what an external system needs to speak our enums (§8.3).

Four read-only endpoints so an integrator can resolve a label name to an id, find
the open iteration, name an assignee, and discover which ``ai_context`` fields
this tenant accepts. Without them every consumer hard-codes UUIDs copied out of a
database, and the first rename breaks all of them silently.

⚠️ **``/meta/users`` returns id and display name only — never email.** §8.3 states
it as a rule, and the reason is that this endpoint is reachable by any token with
``meta:read``, including a service token held by another system entirely. A
directory of everyone's work email is the most reusable thing such a token could
leak, and no consumer needs it to set an assignee.

The enum endpoints are deliberately *not* here: ``type``, ``priority`` and
``status`` values are frozen (§8.3) and published in the OpenAPI document, so a
runtime endpoint would be a second source of truth for something that cannot
change without a v2.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from relay.api.problems import DEFAULT_ERROR_RESPONSES
from relay.api.v1.dependencies import MetaRead
from relay.app.accounts import profile
from relay.app.tickets.metadata import BoardMetadataService

router = APIRouter(
    prefix="/api/v1/meta",
    tags=["meta (v1)"],
    # §8.6 · the error shape is part of the contract, so it is in the
    # document rather than something an integrator discovers by failing.
    responses=DEFAULT_ERROR_RESPONSES,
)


class LabelResponse(BaseModel):
    id: uuid.UUID
    name: str
    color: str


class IterationResponse(BaseModel):
    id: uuid.UUID
    name: str
    starts_on: dt.date | None
    ends_on: dt.date | None
    closed: bool


class UserResponse(BaseModel):
    """Id and display name. See the module note on why there is no email here."""

    id: uuid.UUID
    display_name: str


class TicketFieldResponse(BaseModel):
    key: str
    label: str
    type: str
    #: Non-null means the field is gated on a domain scope (§7.3): this tenant has
    #: it because somebody granted it, not by default. Published so a consumer can
    #: tell "this tenant has no such field" from "you may not write it".
    domain_scope: str | None
    #: A UI preference, and it deliberately does **not** gate writes. Said out
    #: loud here because an integrator would otherwise reasonably assume that
    #: ``visible: false`` means "do not send this".
    visible: bool


@router.get("/labels", response_model=list[LabelResponse])
def labels(token: MetaRead) -> list[LabelResponse]:
    return [
        LabelResponse(id=one.id, name=one.name, color=one.color)
        for one in BoardMetadataService().labels()
    ]


@router.get("/iterations", response_model=list[IterationResponse])
def iterations(token: MetaRead, include_closed: bool = True) -> list[IterationResponse]:
    return [
        IterationResponse(
            id=one.id,
            name=one.name,
            starts_on=one.starts_on,
            ends_on=one.ends_on,
            closed=one.closed,
        )
        for one in BoardMetadataService().iterations(include_closed=include_closed)
    ]


@router.get("/users", response_model=list[UserResponse])
def users(token: MetaRead, limit: int = 200) -> list[UserResponse]:
    """Members, for resolving an assignee. **No emails, and no handles either.**

    The web directory returns the mention handle because people type it; this one
    does not, because the handle is the local part of a work address and handing a
    machine principal the shape of everyone's email is the leak §8.3 is guarding
    against.
    """
    return [
        UserResponse(id=one.user_id, display_name=one.display_name)
        for one in profile.members(limit=limit)
    ]


@router.get("/ticket-fields", response_model=list[TicketFieldResponse])
def ticket_fields(token: MetaRead) -> list[TicketFieldResponse]:
    """The tenant's ``ai_context`` schema (TKT-2 · §7.3).

    This is what makes ``ai_context`` writable by an external system at all: the
    write path validates against exactly this config, so a consumer that reads it
    first knows which keys will be accepted instead of discovering it from a 422.
    """
    return [
        TicketFieldResponse(
            key=one.field.key,
            label=one.field.label,
            type=str(one.field.type),
            domain_scope=one.field.domain_scope,
            visible=one.visible,
        )
        for one in BoardMetadataService().ticket_fields()
    ]
