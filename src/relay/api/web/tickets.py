"""TKT-1…TKT-9 · the board's HTTP surface.

Two conventions here are deliberately the **same** as the public API's, and one
is deliberately different.

Same: **``If-Match: <rev>`` on every mutation**, and an opaque cursor for paging.
§8.4 makes ``rev`` the optimistic-concurrency mechanism and API-3 requires the
header; a web layer that accepted "last write wins" would be a second
concurrency policy, and the loser of a race would silently overwrite the winner
with no error anywhere. So the UI carries the rev it rendered, and a 409 tells it
what the current one is. The parser is literally shared —
``relay.api.revisions`` — so the two surfaces cannot diverge on what a valid
``If-Match`` looks like.

Same: **transitions are their own endpoint.** §8.3 separates them from PATCH
because a status move validates against the state machine and may require a
reason — and because ``TicketService.update`` has no status parameter at all, so
there is no path that writes ``status`` without writing history.

Different: the path accepts **a UUID, a number, or ``RL-331``**. A permalink
carries ``/{tenant_slug}/t/331`` (S-12), a list response carries ids, and making
the frontend keep two lookup paths straight buys nothing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, status
from pydantic import BaseModel, Field

from relay.api import pagination
from relay.api.dependencies import Session
from relay.api.revisions import parse_if_match
from relay.app.errors import ValidationFailed
from relay.app.tickets.comments import CommentService
from relay.app.tickets.service import (
    UNSET,
    ExternalRef,
    NewTicket,
    TicketFilters,
    TicketService,
    TicketView,
)
from relay.domain.enums import Priority, TicketStatus, TicketType
from relay.domain.tickets import TICKET_KEY_PREFIX

router = APIRouter(prefix="/web/tickets", tags=["tickets"])

BAD_KEY = "工单标识必须是编号（如 331 或 RL-331）或 id。"


class TicketResponse(BaseModel):
    id: uuid.UUID
    number: int
    #: ``RL-331``. Frozen on release (S-12) — it is in every permalink and in
    #: every consumer's stored rows.
    key: str
    type: TicketType
    title: str
    description: str
    status: TicketStatus
    priority: Priority
    assignee_id: uuid.UUID | None
    reporter_id: uuid.UUID | None
    iteration_id: uuid.UUID | None
    label_ids: list[uuid.UUID]
    pr_url: str | None
    ai_context: dict[str, Any]
    rev: int
    submitter: dict[str, Any] | None
    source: str | None
    created_at: dt.datetime | None
    updated_at: dt.datetime | None


class TicketPage(BaseModel):
    items: list[TicketResponse]
    #: Absent when there is no next page. Opaque: reading it makes the sort order
    #: part of the contract (§8.6).
    next_cursor: str | None = None


class ExternalRefPayload(BaseModel):
    system: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=200)
    external_url: str | None = None


class CreateTicketPayload(BaseModel):
    type: TicketType
    title: str = Field(min_length=1)
    description: str = ""
    priority: Priority = Priority.P2
    assignee_id: uuid.UUID | None = None
    iteration_id: uuid.UUID | None = None
    label_ids: list[uuid.UUID] = Field(default_factory=list)
    pr_url: str | None = None
    #: Validated against the tenant's ``ai_context_field_config`` (§7.3), never
    #: stored as arbitrary JSON.
    ai_context: dict[str, Any] = Field(default_factory=dict)
    external_ref: ExternalRefPayload | None = None


class UpdateTicketPayload(BaseModel):
    """Absent means unchanged; explicit ``null`` means clear.

    No ``status`` field, on purpose: it would be a second way to move a ticket,
    and the one that skips the state machine.
    """

    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    assignee_id: uuid.UUID | None = None
    iteration_id: uuid.UUID | None = None
    label_ids: list[uuid.UUID] | None = None
    pr_url: str | None = None
    ai_context: dict[str, Any] | None = None


class TransitionPayload(BaseModel):
    to: TicketStatus
    #: Required for Blocked and Won't Fix (TKT-3). The refusal names which.
    reason: str | None = None


class CommentResponse(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str
    created_at: dt.datetime | None
    #: Who was actually notified. A handle that matched nobody — or somebody who
    #: cannot read this ticket (S-21) — is not here, and that is not an error.
    mentioned: list[uuid.UUID]


class CreateCommentPayload(BaseModel):
    body: str = Field(min_length=1)


class HistoryResponse(BaseModel):
    from_status: TicketStatus | None
    to_status: TicketStatus
    actor_id: uuid.UUID | None
    #: §8.4: whether a person dragged a card or an integration called the API.
    #: Cannot be reconstructed after the fact, which is why it is stored.
    actor_type: str
    origin: str
    reason: str | None
    created_at: dt.datetime | None


def _ticket(view: TicketView) -> TicketResponse:
    return TicketResponse(
        id=view.id,
        number=view.number,
        key=view.key,
        type=view.type,
        title=view.title,
        description=view.description,
        status=view.status,
        priority=view.priority,
        assignee_id=view.assignee_id,
        reporter_id=view.reporter_id,
        iteration_id=view.iteration_id,
        label_ids=list(view.label_ids),
        pr_url=view.pr_url,
        ai_context=view.ai_context,
        rev=view.rev,
        submitter=view.submitter,
        source=view.source,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _resolve(key: str) -> TicketView:
    """``RL-331``, ``331`` or a UUID — see the module note."""
    service = TicketService()
    candidate = key.strip()
    if candidate.upper().startswith(TICKET_KEY_PREFIX):
        candidate = candidate[len(TICKET_KEY_PREFIX) :]
    if candidate.isdigit():
        return service.by_number(int(candidate))
    try:
        return service.get(uuid.UUID(key))
    except ValueError as exc:
        raise ValidationFailed(BAD_KEY) from exc


@router.get("", response_model=TicketPage)
def list_tickets(
    session: Session,
    status_in: Annotated[list[TicketStatus] | None, Query(alias="status")] = None,
    priority_in: Annotated[list[Priority] | None, Query(alias="priority")] = None,
    assignee_id: uuid.UUID | None = None,
    label_id: uuid.UUID | None = None,
    iteration_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TicketPage:
    """TKT-5's list, newest-updated first, keyset-paged.

    A Guest gets only their own tickets here (S-21) — the filter is in SQL, so it
    is not something this route could forget.
    """
    filters = TicketFilters(
        status=tuple(status_in or ()),
        priority=tuple(priority_in or ()),
        assignee_id=assignee_id,
        label_id=label_id,
        iteration_id=iteration_id,
        before=pagination.decode(cursor) if cursor else None,
    )
    items = TicketService().list(filters, limit=limit)
    next_cursor = None
    if len(items) == limit and items[-1].updated_at is not None:
        # Only when the page was full: a short page is the last page, and
        # handing out a cursor for it would cost the client one empty request.
        next_cursor = pagination.encode(items[-1].updated_at, items[-1].id)
    return TicketPage(items=[_ticket(one) for one in items], next_cursor=next_cursor)


@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(payload: CreateTicketPayload, session: Session) -> TicketResponse:
    """201, or **200 with the existing ticket** when ``external_ref`` matched.

    The dedupe lives in the use case (API-3), and this is where it becomes
    visible: a repeated create is not an error, it is a pointer to the ticket
    that already exists.
    """
    view = TicketService().create(
        NewTicket(
            type=payload.type,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            assignee_id=payload.assignee_id,
            iteration_id=payload.iteration_id,
            label_ids=tuple(payload.label_ids),
            pr_url=payload.pr_url,
            ai_context=payload.ai_context,
            external_ref=(
                ExternalRef(
                    system=payload.external_ref.system,
                    external_id=payload.external_ref.external_id,
                    external_url=payload.external_ref.external_url,
                )
                if payload.external_ref
                else None
            ),
        )
    )
    return _ticket(view)


@router.get("/{key}", response_model=TicketResponse)
def get_ticket(key: str, session: Session) -> TicketResponse:
    return _ticket(_resolve(key))


@router.patch("/{key}", response_model=TicketResponse)
def update_ticket(
    key: str,
    payload: UpdateTicketPayload,
    session: Session,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TicketResponse:
    expected_rev = parse_if_match(if_match)
    sent = payload.model_fields_set
    current = _resolve(key)
    return _ticket(
        TicketService().update(
            current.id,
            expected_rev=expected_rev,
            title=payload.title if "title" in sent else UNSET,
            description=payload.description if "description" in sent else UNSET,
            priority=payload.priority if "priority" in sent else UNSET,
            assignee_id=payload.assignee_id if "assignee_id" in sent else UNSET,
            iteration_id=payload.iteration_id if "iteration_id" in sent else UNSET,
            label_ids=(
                tuple(payload.label_ids or ()) if "label_ids" in sent else UNSET
            ),
            pr_url=payload.pr_url if "pr_url" in sent else UNSET,
            ai_context=payload.ai_context if "ai_context" in sent else UNSET,
        )
    )


@router.post("/{key}/transitions", response_model=TicketResponse)
def transition_ticket(
    key: str,
    payload: TransitionPayload,
    session: Session,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TicketResponse:
    expected_rev = parse_if_match(if_match)
    current = _resolve(key)
    return _ticket(
        TicketService().transition(
            current.id, payload.to, expected_rev=expected_rev, reason=payload.reason
        )
    )


@router.get("/{key}/comments", response_model=list[CommentResponse])
def list_comments(key: str, session: Session, limit: int = 200) -> list[CommentResponse]:
    ticket = _resolve(key)
    return [
        CommentResponse(
            id=one.id,
            ticket_id=one.ticket_id,
            author_id=one.author_id,
            body=one.body,
            created_at=one.created_at,
            mentioned=list(one.mentioned),
        )
        for one in CommentService().list(ticket.id, limit=limit)
    ]


@router.post(
    "/{key}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED
)
def add_comment(
    key: str, payload: CreateCommentPayload, session: Session
) -> CommentResponse:
    ticket = _resolve(key)
    view = CommentService().add(ticket.id, payload.body)
    return CommentResponse(
        id=view.id,
        ticket_id=view.ticket_id,
        author_id=view.author_id,
        body=view.body,
        created_at=view.created_at,
        mentioned=list(view.mentioned),
    )


@router.get("/{key}/history", response_model=list[HistoryResponse])
def ticket_history(key: str, session: Session) -> list[HistoryResponse]:
    ticket = _resolve(key)
    return [
        HistoryResponse(
            from_status=row.from_status,
            to_status=row.to_status,
            actor_id=row.actor_id,
            actor_type=str(row.actor_type),
            origin=str(row.origin),
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in TicketService().history(ticket.id)
    ]
