"""API-2/3/6 · the ticket resources (design §8.3, §8.4, §8.8).

**One implementation, two surfaces.** Every route below calls the same
``TicketService`` / ``CommentService`` the Web UI calls. The differences are
contract-level and all of them are deliberate:

* the path accepts **``RL-331`` or ``331``, never a UUID.** The web surface takes
  ids because its own list responses hand them out; the public contract is
  narrower on purpose, because everything it accepts is frozen (§8.6) and there
  is no reason to promise external systems that our primary keys are addressable
  forever;
* every response carries **``url``**, the permalink with its tenant segment
  (S-12). The first consumer stores that URL against its own records, so adding
  the segment later would be a breaking change in somebody else's database;
* ``POST`` honours **``Idempotency-Key``** and returns **200 with the existing
  ticket** when ``external_ref`` matched — §8.4 wants both defences, against the
  network and against the upstream respectively;
* ``PATCH`` **requires ``If-Match``** and answers 409 with the current ``rev``.

**API-6 lives here too**: ``submitter`` and ``source``. ``submitter`` is *not*
``reporter`` (S-10) — gateway users are not Relay accounts and should not have to
be — so it is display and traceability only: no permission effect, and excluded
from every people-metric (INT-8).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Header, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, EmailStr, Field

from relay.api import pagination
from relay.api.problems import DEFAULT_ERROR_RESPONSES
from relay.api.revisions import parse_if_match
from relay.api.v1.dependencies import (
    CommentsWrite,
    TicketsRead,
    TicketsWrite,
    idempotent,
    ticket_url,
)
from relay.api.wiring import blob_store
from relay.app import idempotency
from relay.app.errors import ValidationFailed
from relay.app.logs.attachments import AttachmentService
from relay.app.tickets.comments import CommentService
from relay.app.tickets.service import (
    UNSET,
    ExternalRef,
    NewTicket,
    TicketFilters,
    TicketService,
    TicketView,
)
from relay.domain.enums import Priority, SupportCategory, TicketStatus, TicketType
from relay.domain.tickets import TICKET_KEY_PREFIX

router = APIRouter(
    prefix="/api/v1/tickets",
    tags=["tickets (v1)"],
    # §8.6 · the error shape is part of the contract, so it is in the
    # document rather than something an integrator discovers by failing.
    responses=DEFAULT_ERROR_RESPONSES,
)

BAD_KEY = "工单标识必须是编号（如 331 或 RL-331）。"

#: §8.8 item 4: feedback text is untrusted human input and S1 has no DLP, so the
#: minimum measure available at this layer is a length bound. Generous enough for
#: a stack trace, small enough that a paste of a log file is refused rather than
#: stored forever.
MAX_DESCRIPTION = 20_000
MAX_COMMENT = 20_000


class Submitter(BaseModel):
    """API-6 · the real person behind a machine-filed ticket (§8.8).

    Structured rather than free text in the description so that "who reported
    this?" stays answerable, and so the UI can render "submitted by X via the
    gateway WebUI" without parsing prose.

    ⚠️ **No permission effect.** A submitter is not an account: they cannot read
    the ticket, are not notified by Relay (F-6 ② puts that on the consumer), and
    are excluded from every people-metric.
    """

    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    external_id: str | None = Field(default=None, max_length=200)


class ExternalRefPayload(BaseModel):
    system: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=200)
    external_url: str | None = Field(default=None, max_length=1024)


class TicketResponse(BaseModel):
    """§8.3's wire shape. **Frozen on release** — additive change only."""

    id: uuid.UUID
    number: int
    key: str
    #: The permalink, tenant segment included (S-12).
    url: str
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
    #: §8.4. Carry it on the next ``PATCH`` as ``If-Match``; consumers also use it
    #: to drop out-of-order webhook events.
    rev: int
    submitter: dict[str, Any] | None
    source: str | None
    #: Gateway support-ticket category. Null on ordinary investigation tickets.
    category: SupportCategory | None
    #: Label names, in the same order as ``label_ids``. §8.3's create example
    #: sends names; echoing them saves the consumer a ``/meta/labels`` round-trip.
    labels: list[str]
    external_ref: ExternalRefPayload | None = None
    created_at: dt.datetime | None
    updated_at: dt.datetime | None


class TicketPage(BaseModel):
    items: list[TicketResponse]
    #: Opaque, and absent on the last page. Reading it would make our sort order
    #: part of the contract (§8.6).
    next_cursor: str | None = None


class CreateTicketPayload(BaseModel):
    type: TicketType
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    priority: Priority = Priority.P2
    assignee_id: uuid.UUID | None = None
    iteration_id: uuid.UUID | None = None
    label_ids: list[uuid.UUID] = Field(default_factory=list)
    pr_url: str | None = Field(default=None, max_length=1024)
    #: Validated against the tenant's ``ai_context_field_config`` (§7.3). An
    #: external system cannot invent fields — §8.8 item 4 is explicit that
    #: arbitrary JSON from a feedback form must not reach the column.
    ai_context: dict[str, Any] = Field(default_factory=dict)
    external_ref: ExternalRefPayload | None = None
    submitter: Submitter | None = None
    #: A fixed label naming the surface, e.g. ``gateway-webui``.
    source: str | None = Field(default=None, max_length=64)
    category: SupportCategory | None = None
    #: Names, resolved or created. Coexists with ``label_ids``.
    labels: list[str] = Field(default_factory=list)


class UpdateTicketPayload(BaseModel):
    """Absent means unchanged; explicit ``null`` means clear.

    No ``status``: moving a ticket goes through ``/transitions``, which is the
    only path that validates against the state machine and writes history.
    """

    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    priority: Priority | None = None
    assignee_id: uuid.UUID | None = None
    iteration_id: uuid.UUID | None = None
    label_ids: list[uuid.UUID] | None = None
    pr_url: str | None = Field(default=None, max_length=1024)
    ai_context: dict[str, Any] | None = None
    category: SupportCategory | None = None


class TransitionPayload(BaseModel):
    to: TicketStatus
    #: Optional note on the history row. No status currently requires one
    #: (clarification 2.2 dropped Blocked / Won't Fix).
    reason: str | None = Field(default=None, max_length=2000)


class CommentResponse(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str
    created_at: dt.datetime | None
    #: Who was actually notified. A handle matching nobody — or somebody who
    #: cannot read this ticket (S-21) — is absent, and that is not an error.
    mentioned: list[uuid.UUID]


class CreateCommentPayload(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_COMMENT)


class HistoryResponse(BaseModel):
    from_status: TicketStatus | None
    to_status: TicketStatus
    actor_id: uuid.UUID | None
    #: §8.4: a person in the UI, or an integration over this API. Not
    #: reconstructable after the fact, which is why it is stored.
    actor_type: str
    origin: str
    reason: str | None
    created_at: dt.datetime | None


def _ticket(view: TicketView, tenant_slug: str) -> TicketResponse:
    return TicketResponse(
        id=view.id,
        number=view.number,
        key=view.key,
        url=ticket_url(tenant_slug, view.number),
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
        category=view.category,
        labels=list(view.labels),
        external_ref=(
            ExternalRefPayload(
                system=view.external_ref.system,
                external_id=view.external_ref.external_id,
                external_url=view.external_ref.external_url,
            )
            if view.external_ref
            else None
        ),
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


BAD_INSTANT = (
    "updated_since 必须是 ISO 8601 时间戳，例如 2026-08-24T10:00:00+08:00。"
)


def _instant(candidate: str | None) -> dt.datetime | None:
    """Parse ``updated_since`` **tolerantly about one thing**: the ``+`` in an offset.

    The value a consumer has is the ``updated_at`` from a previous response —
    ``2026-08-24T10:00:00+08:00``. Pasted into a query string without
    percent-encoding, its ``+`` decodes to a space, and a strict parse then
    answers 422 to a caller who copied our own output. Almost every consumer will
    write it that way at least once.

    So a space where an offset's sign belongs is repaired here, and *only* that:
    anything else is still refused, because a silently-misparsed watermark means a
    poll that either misses changes or replays the whole board.
    """
    if candidate is None:
        return None
    text = candidate.strip()
    if len(text) > 11 and text[-6] == " " and text[-3] == ":":
        text = f"{text[:-6]}+{text[-5:]}"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationFailed(BAD_INSTANT, detail={"parameter": "updated_since"}) from exc


def _resolve(key: str) -> TicketView:
    """``RL-331`` or ``331``. **Not** a UUID — see the module note."""
    candidate = key.strip()
    if candidate.upper().startswith(TICKET_KEY_PREFIX):
        candidate = candidate[len(TICKET_KEY_PREFIX) :]
    if not candidate.isdigit():
        raise ValidationFailed(BAD_KEY)
    return TicketService().by_number(int(candidate))


@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: CreateTicketPayload,
    request: Request,
    response: Response,
    token: TicketsWrite,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """File a ticket. **201**, or **200** when it already existed.

    Three outcomes, and the distinction between the last two is the whole of
    §8.4's "both defences are needed":

    * new ticket → 201 with ``Location``;
    * the same ``Idempotency-Key`` replayed → the *first* response, verbatim.
      This is the network defence: a lost response, a proxy retry;
    * a matching ``external_ref`` → **200** with the ticket that already exists.
      This is the upstream defence: a user clicking submit three times, or a
      compensating job re-running. It is not an error, because the caller needs
      to be told which ticket to look at rather than given something to retry.
    """
    key, replay = idempotent(request, idempotency_key, payload.model_dump(mode="json"))
    if replay is not None:
        response.status_code = replay.status
        return replay.body

    try:
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
                submitter=payload.submitter.model_dump(mode="json") if payload.submitter else None,
                source=payload.source,
                category=payload.category,
                labels=tuple(payload.labels),
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
    except BaseException:
        # The claim goes back: a caller whose create was refused (a bad label, a
        # rejected ai_context field) must be able to fix the body and retry with
        # the same key. Holding the key would answer "key reused" to somebody who
        # did exactly the right thing.
        if key:
            idempotency.abandon(key)
        raise

    body = _ticket(view, token.tenant_slug)
    code = status.HTTP_200_OK if view.deduped else status.HTTP_201_CREATED
    response.status_code = code
    response.headers["Location"] = f"/api/v1/tickets/{view.key}"
    if key:
        idempotency.complete(key, code, body.model_dump(mode="json"))
    return body


@router.get("", response_model=TicketPage)
def list_tickets(
    token: TicketsRead,
    status_in: Annotated[list[TicketStatus] | None, Query(alias="status")] = None,
    priority_in: Annotated[list[Priority] | None, Query(alias="priority")] = None,
    assignee: uuid.UUID | None = None,
    label: uuid.UUID | None = None,
    iteration: uuid.UUID | None = None,
    category_in: Annotated[list[SupportCategory] | None, Query(alias="category")] = None,
    updated_since: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TicketPage:
    """Newest-updated first, keyset-paged (§8.3).

    ``updated_since`` is what a polling consumer uses, and it is **inclusive** —
    an exclusive bound drops exactly the rows whose timestamp equals the
    watermark the caller stored. Cursor rather than offset because the ordering
    key changes while somebody pages, and offset both skips and repeats rows.

    It is typed as a string rather than a ``datetime`` on purpose; see
    :func:`_instant`.
    """
    filters = TicketFilters(
        status=tuple(status_in or ()),
        priority=tuple(priority_in or ()),
        assignee_id=assignee,
        label_id=label,
        iteration_id=iteration,
        category=tuple(category_in or ()),
        updated_since=_instant(updated_since),
        before=pagination.decode(cursor) if cursor else None,
    )
    items = TicketService().list(filters, limit=limit)
    next_cursor = None
    if len(items) == limit and items[-1].updated_at is not None:
        next_cursor = pagination.encode(items[-1].updated_at, items[-1].id)
    return TicketPage(
        items=[_ticket(one, token.tenant_slug) for one in items], next_cursor=next_cursor
    )


@router.get("/{key}", response_model=TicketResponse)
def get_ticket(key: str, token: TicketsRead) -> TicketResponse:
    """Detail. This is also F-6 ①'s polling endpoint: the feedback consumer reads
    ``status`` and ``updated_at`` from here and shows progress to the submitter.

    ⚠️ Internal comments are **not** hidden from this token — ``/comments`` is
    readable with ``tickets:read``. "Do not show internal comments to the
    submitter" is a constraint on the *consumer* and belongs in the integration
    guide; expecting the API surface to enforce it would be a misreading.
    """
    return _ticket(_resolve(key), token.tenant_slug)


@router.patch("/{key}", response_model=TicketResponse)
def update_ticket(
    key: str,
    payload: UpdateTicketPayload,
    token: TicketsWrite,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TicketResponse:
    """Partial update. ``If-Match: <rev>`` required; 409 carries the current rev.

    Not idempotency-keyed, and that is not an omission: ``rev`` already makes a
    replayed PATCH safe — the second attempt carries a stale rev and is refused
    with the current one, which is exactly the information the client needs.
    """
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
            label_ids=tuple(payload.label_ids or ()) if "label_ids" in sent else UNSET,
            pr_url=payload.pr_url if "pr_url" in sent else UNSET,
            ai_context=payload.ai_context if "ai_context" in sent else UNSET,
            category=payload.category if "category" in sent else UNSET,
        ),
        token.tenant_slug,
    )


@router.post("/{key}/transitions", response_model=TicketResponse)
def transition_ticket(
    key: str,
    payload: TransitionPayload,
    token: TicketsWrite,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TicketResponse:
    """Move a ticket through the state machine (§7.2, S-23).

    Separate from ``PATCH`` because a move validates the edge and may require a
    reason — and because ``TicketService.update`` has no ``status`` parameter at
    all, so no path writes a status without writing history.
    """
    expected_rev = parse_if_match(if_match)
    current = _resolve(key)
    return _ticket(
        TicketService().transition(
            current.id, payload.to, expected_rev=expected_rev, reason=payload.reason
        ),
        token.tenant_slug,
    )


@router.get("/{key}/comments", response_model=list[CommentResponse])
def list_comments(
    key: str,
    token: TicketsRead,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[CommentResponse]:
    ticket = _resolve(key)
    return [_comment(one) for one in CommentService().list(ticket.id, limit=limit)]


@router.post(
    "/{key}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED
)
def add_comment(key: str, payload: CreateCommentPayload, token: CommentsWrite) -> CommentResponse:
    """Comment as this principal — and notify, exactly as the UI does (TKT-4).

    That the notification fires here is the point of §8.1: an API that wrote
    comments without notifying would make the API a silent back door, and the
    symptom ("commenting from the integration doesn't ping anyone") is invisible
    in a diff.
    """
    ticket = _resolve(key)
    return _comment(CommentService().add(ticket.id, payload.body))


@router.get("/{key}/history", response_model=list[HistoryResponse])
def ticket_history(key: str, token: TicketsRead) -> list[HistoryResponse]:
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


class TicketAttachmentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    size: int
    mime: str
    scan_state: str
    uploaded_by: uuid.UUID | None


class TicketAttachmentLinkResponse(BaseModel):
    url: str


@router.post(
    "/{key}/attachments",
    response_model=TicketAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_attachment(
    key: str,
    token: TicketsWrite,
    file: Annotated[UploadFile, File()],
) -> TicketAttachmentResponse:
    """Attach a file to this ticket. Same store and limits as ``/web/attachments``.

    Gateway support tickets carry screenshots; S-26 lets the public API take
    them so the agent workbench sees the same files, without exposing a generic
    owner_type/owner_id upload that could hang a file on a log.
    """
    ticket = _resolve(key)
    view = AttachmentService(blob_store()).upload(
        "ticket",
        ticket.id,
        file.filename or "file",
        file.content_type or "application/octet-stream",
        file.file,
    )
    return _attachment(view)


@router.get("/{key}/attachments", response_model=list[TicketAttachmentResponse])
def list_attachments(key: str, token: TicketsRead) -> list[TicketAttachmentResponse]:
    ticket = _resolve(key)
    return [
        _attachment(one) for one in AttachmentService(blob_store()).list_for("ticket", ticket.id)
    ]


@router.get("/{key}/attachments/{attachment_id}/link", response_model=TicketAttachmentLinkResponse)
def attachment_link(
    key: str, attachment_id: uuid.UUID, token: TicketsRead
) -> TicketAttachmentLinkResponse:
    ticket = _resolve(key)
    service = AttachmentService(blob_store())
    service.require_on(attachment_id, "ticket", ticket.id)
    return TicketAttachmentLinkResponse(url=service.link(attachment_id))


@router.delete("/{key}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(key: str, attachment_id: uuid.UUID, token: TicketsWrite) -> Response:
    ticket = _resolve(key)
    service = AttachmentService(blob_store())
    service.require_on(attachment_id, "ticket", ticket.id)
    service.delete(attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _attachment(view) -> TicketAttachmentResponse:
    return TicketAttachmentResponse(
        id=view.id,
        filename=view.filename,
        size=view.size,
        mime=view.mime,
        scan_state=view.scan_state,
        uploaded_by=view.uploaded_by,
    )


def _comment(view) -> CommentResponse:
    return CommentResponse(
        id=view.id,
        ticket_id=view.ticket_id,
        author_id=view.author_id,
        body=view.body,
        created_at=view.created_at,
        mentioned=list(view.mentioned),
    )
