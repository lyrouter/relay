"""TKT-8 · labels, iterations, and the ``ai_context`` field config.

The board's vocabulary. Two things worth knowing before reading:

**Closing an iteration does not move its tickets.** ``set_iteration_closed`` is
explicit about that, and the endpoint inherits it: a closed iteration is a
statement about the sprint, not about the work. Moving unfinished tickets is a
decision made ticket by ticket — doing it here would rewrite an assignee's board
overnight.

**``/meta/ticket-fields`` is a read for everybody, including a Guest.** The
fields are not a secret and the ticket detail page is already showing their
values; hiding the labels would leave it rendering ``routing_policy`` as a raw
key. Editing the config is ``AI_CONTEXT_CONFIG`` and has no endpoint in S1 —
§7.3 seeds it at bootstrap and nothing in the product changes it yet.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from relay.api.dependencies import Session
from relay.app.tickets.metadata import BoardMetadataService
from relay.domain.enums import AiContextFieldType

router = APIRouter(prefix="/web/meta", tags=["meta"])


class LabelResponse(BaseModel):
    id: uuid.UUID
    name: str
    #: Hex, validated: it is rendered into a style attribute, so an unchecked
    #: value is CSS injection rather than a cosmetic problem.
    color: str


class LabelPayload(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str = "#6b7280"


class RenameLabelPayload(BaseModel):
    name: str | None = None
    color: str | None = None


class IterationResponse(BaseModel):
    id: uuid.UUID
    name: str
    starts_on: dt.date | None
    ends_on: dt.date | None
    closed: bool


class IterationPayload(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None


class ClosePayload(BaseModel):
    closed: bool


class TicketFieldResponse(BaseModel):
    key: str
    label: str
    type: AiContextFieldType
    #: Non-null means the field is gated on a domain scope (§7.3) — the tenant
    #: has it because somebody granted it, not by default.
    domain_scope: str | None
    #: A UI preference. It deliberately does **not** gate writes.
    visible: bool


@router.get("/labels", response_model=list[LabelResponse])
def list_labels(session: Session) -> list[LabelResponse]:
    return [LabelResponse(id=one.id, name=one.name, color=one.color) for one in
            BoardMetadataService().labels()]


@router.post("/labels", response_model=LabelResponse, status_code=status.HTTP_201_CREATED)
def create_label(payload: LabelPayload, session: Session) -> LabelResponse:
    one = BoardMetadataService().create_label(payload.name, payload.color)
    return LabelResponse(id=one.id, name=one.name, color=one.color)


@router.patch("/labels/{label_id}", response_model=LabelResponse)
def rename_label(
    label_id: uuid.UUID, payload: RenameLabelPayload, session: Session
) -> LabelResponse:
    one = BoardMetadataService().rename_label(label_id, payload.name, payload.color)
    return LabelResponse(id=one.id, name=one.name, color=one.color)


@router.get("/iterations", response_model=list[IterationResponse])
def list_iterations(session: Session, include_closed: bool = True) -> list[IterationResponse]:
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


@router.post(
    "/iterations", response_model=IterationResponse, status_code=status.HTTP_201_CREATED
)
def create_iteration(payload: IterationPayload, session: Session) -> IterationResponse:
    one = BoardMetadataService().create_iteration(
        payload.name, payload.starts_on, payload.ends_on
    )
    return IterationResponse(
        id=one.id, name=one.name, starts_on=one.starts_on, ends_on=one.ends_on, closed=one.closed
    )


@router.put("/iterations/{iteration_id}/closed", response_model=IterationResponse)
def close_iteration(
    iteration_id: uuid.UUID, payload: ClosePayload, session: Session
) -> IterationResponse:
    one = BoardMetadataService().set_iteration_closed(iteration_id, payload.closed)
    return IterationResponse(
        id=one.id, name=one.name, starts_on=one.starts_on, ends_on=one.ends_on, closed=one.closed
    )


@router.get("/ticket-fields", response_model=list[TicketFieldResponse])
def ticket_fields(session: Session) -> list[TicketFieldResponse]:
    return [
        TicketFieldResponse(
            key=one.field.key,
            label=one.field.label,
            type=one.field.type,
            domain_scope=one.field.domain_scope,
            visible=one.visible,
        )
        for one in BoardMetadataService().ticket_fields()
    ]
