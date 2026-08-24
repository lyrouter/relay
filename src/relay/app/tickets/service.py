"""TKT-1 / TKT-3 / TKT-4 · the ticket use cases.

The shared implementation §8.1 insists on: the web UI and the public API both
land here. Which means a few things that look like API concerns are enforced at
this level on purpose —

* **``rev`` is required on every mutation.** API-3 makes ``If-Match: <rev>``
  mandatory on PATCH with a 409 carrying the current value; a UI that could skip
  it would be a second concurrency policy, and the loser of a race would silently
  overwrite the winner.
* **``external_ref`` dedupe lives here.** A repeated create returns the existing
  ticket rather than a second one (API-3). Alert replays and webhook
  redeliveries are not an API-layer problem — they are a fact about what a
  ticket *is*.
* **Status only moves through :meth:`transition`.** There is no code path that
  writes ``status`` without writing ``ticket_status_history``, because a
  transition with no history row is exactly the data Phase 2's GH loop guard
  needs and cannot reconstruct.

**Tickets carry no share level**, so for an Admin or a Member they are
tenant-wide — L3 by construction. **A Guest is the exception (decision S-21):**
they read only the tickets they are the assignee or reporter of. The rule lives
in :mod:`relay.app.tickets.sharing` with a SQL mirror for the list and for
search, and refusal is ``NotFound`` — a ticket a Guest may not read is one they
should not learn exists. No per-ticket ACL column was added: the role already
carries the distinction the decision needed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from relay.app import audit, notifications
from relay.app.authz import Principal, actor_principal, require
from relay.app.errors import Conflict, NotFound, ValidationFailed
from relay.app.notifications import NotificationEvent
from relay.app.tickets.ai_context import validate_write
from relay.app.tickets.numbering import next_number
from relay.app.tickets.sharing import TicketReader, can_read_ticket
from relay.context import current_context
from relay.domain.ai_context import InvalidAiContext
from relay.domain.enums import (
    NotificationType,
    Priority,
    TicketStatus,
    TicketType,
    UserStatus,
)
from relay.domain.permissions import Capability
from relay.domain.tickets import check_transition, ticket_key
from relay.infra.db.models import (
    Iteration,
    Label,
    Ticket,
    TicketExternalRef,
    TicketLabel,
    TicketStatusHistory,
    User,
)
from relay.infra.db.session import tenant_session
from relay.infra.db.visibility import visible_tickets_predicate

TICKET_NOT_FOUND = "找不到该工单。"
ASSIGNEE_NOT_FOUND = "指定的负责人不存在或已停用。"
ITERATION_NOT_FOUND = "指定的迭代不存在。"
LABEL_NOT_FOUND = "指定的标签不存在。"
TITLE_REQUIRED = "标题不能为空。"
REV_MISMATCH = "该工单已被其他人修改，请刷新后重试。"

#: "Absent" in a patch, as distinct from an explicit ``None`` meaning "clear it".
UNSET: Any = object()


@dataclass(frozen=True, slots=True)
class ExternalRef:
    """§8.4. The business-level dedupe key: one external record, one ticket."""

    system: str
    external_id: str
    external_url: str | None = None


@dataclass(frozen=True, slots=True)
class NewTicket:
    type: TicketType
    title: str
    description: str = ""
    priority: Priority = Priority.P2
    assignee_id: uuid.UUID | None = None
    iteration_id: uuid.UUID | None = None
    label_ids: tuple[uuid.UUID, ...] = ()
    #: TKT-8: a plain link. No status write-back, no CI or review state.
    pr_url: str | None = None
    ai_context: dict = field(default_factory=dict)
    #: API-6 / §8.8. **Not** the reporter (S-10): gateway users are not Relay
    #: accounts. Display and traceability only — no permission effect, and
    #: excluded from every people-metric.
    submitter: dict | None = None
    source: str | None = None
    external_ref: ExternalRef | None = None


@dataclass(frozen=True, slots=True)
class TicketView:
    id: uuid.UUID
    number: int
    key: str
    type: TicketType
    title: str
    description: str
    status: TicketStatus
    priority: Priority
    assignee_id: uuid.UUID | None
    reporter_id: uuid.UUID | None
    iteration_id: uuid.UUID | None
    label_ids: tuple[uuid.UUID, ...]
    pr_url: str | None
    ai_context: dict
    rev: int
    submitter: dict | None
    source: str | None
    #: F-6 ①: the feedback consumer polls ``GET /tickets/{key}`` and shows
    #: **status and last-updated only**. Both are here so that path never needs
    #: a second query, and so a list response can carry its own cursor.
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    #: True when this create was deduped against an existing external_ref.
    deduped: bool = False


@dataclass(frozen=True, slots=True)
class TicketFilters:
    """TKT-5's filter set, minus the keyword.

    Keyword search deliberately waits for LOG-8, which owns the one text-search
    implementation (PG FTS + pgroonga behind ``SearchPort``). Adding an ILIKE
    here would give the product two search behaviours that disagree about
    Chinese tokenisation, and the cheap one would be the one people hit first.
    """

    status: tuple[TicketStatus, ...] = ()
    assignee_id: uuid.UUID | None = None
    priority: tuple[Priority, ...] = ()
    label_id: uuid.UUID | None = None
    iteration_id: uuid.UUID | None = None
    #: Keyset cursor: return rows strictly older than this (updated_at, id).
    #: API-2 encodes and decodes the opaque form; this is what it carries.
    before: tuple[dt.datetime, uuid.UUID] | None = None


class TicketService:
    """Runs inside an established ``TenantContext``."""

    # ---------------------------------------------------------------- create

    def create(self, new: NewTicket, *, now: dt.datetime | None = None) -> TicketView:
        now = now or dt.datetime.now(dt.UTC)
        ctx = current_context()
        title = new.title.strip()
        if not title:
            raise ValidationFailed(TITLE_REQUIRED)

        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.TICKET_WRITE)

            if new.external_ref is not None:
                existing = self._find_by_external_ref(session, new.external_ref)
                if existing is not None:
                    # API-3: a repeated create is refused and returns the ticket
                    # that already exists. An alert that fires twice must not
                    # produce two tickets, and the second caller needs to be
                    # told which one to look at, not given an error to retry.
                    return _view(session, existing, deduped=True)

            _check_assignee(session, new.assignee_id)
            _check_iteration(session, new.iteration_id)
            _check_labels(session, new.label_ids)
            try:
                ai_context = validate_write(session, new.ai_context)
            except InvalidAiContext as exc:
                raise ValidationFailed(str(exc)) from exc

            ticket = Ticket(
                tenant_id=ctx.tenant_id,
                number=next_number(session, ctx.tenant_id),
                type=new.type,
                title=title,
                description=new.description,
                status=TicketStatus.TODO,
                priority=new.priority,
                assignee_id=new.assignee_id,
                # None for a service principal, which is why INT-8 excludes
                # service principals from people-metrics rather than counting a
                # machine as a reporter.
                reporter_id=actor.user_id,
                iteration_id=new.iteration_id,
                pr_url=_clean_pr_url(new.pr_url),
                ai_context=ai_context,
                rev=1,
                submitter=new.submitter,
                source=new.source,
                actor_type=ctx.actor_type,
                origin=ctx.origin,
            )
            session.add(ticket)
            session.flush()

            for label_id in dict.fromkeys(new.label_ids):
                session.add(
                    TicketLabel(tenant_id=ctx.tenant_id, ticket_id=ticket.id, label_id=label_id)
                )

            # The opening row of the history. Without it a ticket's first status
            # is the only one with no record of who set it.
            session.add(
                TicketStatusHistory(
                    tenant_id=ctx.tenant_id,
                    ticket_id=ticket.id,
                    from_status=None,
                    to_status=TicketStatus.TODO,
                    actor_id=ctx.actor_id,
                    actor_type=ctx.actor_type,
                    origin=ctx.origin,
                )
            )

            if new.external_ref is not None:
                session.add(
                    TicketExternalRef(
                        tenant_id=ctx.tenant_id,
                        ticket_id=ticket.id,
                        system=new.external_ref.system,
                        external_id=new.external_ref.external_id,
                        external_url=new.external_ref.external_url,
                    )
                )

            if ticket.assignee_id is not None:
                notifications.emit(
                    session, _assignment_event(ticket, ticket.assignee_id), now=now
                )

            audit.record(
                session,
                "ticket.created",
                target_type="ticket",
                target_id=ticket.id,
                after={"key": ticket_key(ticket.number), "title": title},
            )
            view = _view(session, ticket)
            session.commit()
            return view

    # ---------------------------------------------------------------- update

    def update(
        self,
        ticket_id: uuid.UUID,
        *,
        expected_rev: int,
        title: Any = UNSET,
        description: Any = UNSET,
        priority: Any = UNSET,
        assignee_id: Any = UNSET,
        iteration_id: Any = UNSET,
        label_ids: Any = UNSET,
        pr_url: Any = UNSET,
        ai_context: Any = UNSET,
        now: dt.datetime | None = None,
    ) -> TicketView:
        """Patch a ticket. Absent means unchanged; explicit None means clear.

        ``ai_context`` **replaces** the whole object rather than merging into it.
        It is one JSONB field, and merge semantics would leave no way to remove a
        key — which matters because these fields are written by external systems
        that get things wrong and need to be able to take them back.
        """
        now = now or dt.datetime.now(dt.UTC)

        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.TICKET_WRITE)
            ticket = _load(session, ticket_id)
            _check_rev(ticket, expected_rev)

            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            previous_assignee = ticket.assignee_id

            if title is not UNSET:
                clean = (title or "").strip()
                if not clean:
                    raise ValidationFailed(TITLE_REQUIRED)
                before["title"], after["title"] = ticket.title, clean
                ticket.title = clean
            if description is not UNSET:
                ticket.description = description or ""
                after["description_len"] = len(ticket.description)
            if priority is not UNSET:
                before["priority"], after["priority"] = str(ticket.priority), str(priority)
                ticket.priority = priority
            if assignee_id is not UNSET:
                _check_assignee(session, assignee_id)
                before["assignee_id"] = str(ticket.assignee_id) if ticket.assignee_id else None
                after["assignee_id"] = str(assignee_id) if assignee_id else None
                ticket.assignee_id = assignee_id
            if iteration_id is not UNSET:
                _check_iteration(session, iteration_id)
                ticket.iteration_id = iteration_id
                after["iteration_id"] = str(iteration_id) if iteration_id else None
            if pr_url is not UNSET:
                ticket.pr_url = _clean_pr_url(pr_url)
                after["pr_url"] = ticket.pr_url
            if ai_context is not UNSET:
                try:
                    ticket.ai_context = validate_write(session, ai_context or {})
                except InvalidAiContext as exc:
                    raise ValidationFailed(str(exc)) from exc
                after["ai_context_keys"] = sorted(ticket.ai_context)
            if label_ids is not UNSET:
                wanted = tuple(dict.fromkeys(label_ids or ()))
                _check_labels(session, wanted)
                _replace_labels(session, ticket, wanted)
                after["label_ids"] = [str(one) for one in wanted]

            ticket.rev += 1
            after["rev"] = ticket.rev

            if assignee_id is not UNSET and assignee_id and assignee_id != previous_assignee:
                notifications.emit(session, _assignment_event(ticket, assignee_id), now=now)

            audit.record(
                session,
                "ticket.updated",
                target_type="ticket",
                target_id=ticket.id,
                before=before or None,
                after=after,
            )
            view = _view(session, ticket)
            session.commit()
            return view

    # ------------------------------------------------------------ transition

    def transition(
        self,
        ticket_id: uuid.UUID,
        target: TicketStatus,
        *,
        expected_rev: int,
        reason: str | None = None,
        now: dt.datetime | None = None,
    ) -> TicketView:
        """Move a ticket, writing history and notifying the people who care."""
        now = now or dt.datetime.now(dt.UTC)
        ctx = current_context()

        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.TICKET_WRITE)
            ticket = _load(session, ticket_id)
            _check_rev(ticket, expected_rev)

            from_status = ticket.status
            try:
                check_transition(
                    from_status,
                    target,
                    reason=reason,
                    blocked_from=_blocked_from(session, ticket.id),
                )
            except ValueError as exc:
                # IllegalTransition and ReasonRequired are both domain
                # ValueErrors carrying a message that names the next step.
                raise ValidationFailed(str(exc)) from exc

            ticket.status = target
            ticket.rev += 1
            session.add(
                TicketStatusHistory(
                    tenant_id=ctx.tenant_id,
                    ticket_id=ticket.id,
                    from_status=from_status,
                    to_status=target,
                    actor_id=ctx.actor_id,
                    # §8.4: the column Phase 2's GH loop guard reads. Whether a
                    # human dragged a card or an external system called the API
                    # cannot be reconstructed after the fact.
                    actor_type=ctx.actor_type,
                    origin=ctx.origin,
                    reason=(reason or None),
                )
            )

            event_payload = {
                "key": ticket_key(ticket.number),
                "title": ticket.title,
                "from": str(from_status),
                "to": str(target),
            }
            # Assignee and reporter both, deduplicated, and never the actor —
            # emit() drops a notification addressed to whoever caused it.
            for recipient in dict.fromkeys(
                one for one in (ticket.assignee_id, ticket.reporter_id) if one is not None
            ):
                notifications.emit(
                    session,
                    NotificationEvent(
                        recipient_id=recipient,
                        type=NotificationType.STATUS_CHANGE,
                        target_type="ticket",
                        target_id=ticket.id,
                        payload=event_payload,
                    ),
                    now=now,
                )

            audit.record(
                session,
                "ticket.status_changed",
                target_type="ticket",
                target_id=ticket.id,
                before={"status": str(from_status)},
                after={"status": str(target), "reason": reason, "rev": ticket.rev},
            )
            view = _view(session, ticket)
            session.commit()
            return view

    # ----------------------------------------------------------------- reads

    def get(self, ticket_id: uuid.UUID) -> TicketView:
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.CONTENT_VIEW)
            ticket = _load(session, ticket_id)
            require_readable(actor, ticket)
            return _view(session, ticket)

    def by_number(self, number: int) -> TicketView:
        """Lookup by ``RL-<number>``, which is what a permalink carries."""
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.CONTENT_VIEW)
            ticket = session.scalars(select(Ticket).where(Ticket.number == number)).first()
            if ticket is None:
                raise NotFound(TICKET_NOT_FOUND)
            require_readable(actor, ticket)
            return _view(session, ticket)

    def list(self, filters: TicketFilters | None = None, limit: int = 50) -> list[TicketView]:
        """TKT-5's list, ordered newest-updated first with a keyset cursor.

        Keyset rather than OFFSET: the board is sorted by ``updated_at``, which
        changes while somebody is paging, so OFFSET both skips and repeats rows.
        ``(updated_at, id)`` is stable and rides the
        ``(tenant_id, status, updated_at)`` index MT-4 already built.
        """
        filters = filters or TicketFilters()
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.CONTENT_VIEW)

            # S-21 first, so a Guest's filters run over their own tickets rather
            # than over the board.
            query = select(Ticket).where(visible_tickets_predicate(actor.user_id, actor.role))
            if filters.status:
                query = query.where(Ticket.status.in_(filters.status))
            if filters.assignee_id is not None:
                query = query.where(Ticket.assignee_id == filters.assignee_id)
            if filters.priority:
                query = query.where(Ticket.priority.in_(filters.priority))
            if filters.iteration_id is not None:
                query = query.where(Ticket.iteration_id == filters.iteration_id)
            if filters.label_id is not None:
                query = query.where(
                    Ticket.id.in_(
                        select(TicketLabel.ticket_id).where(
                            TicketLabel.label_id == filters.label_id
                        )
                    )
                )
            if filters.before is not None:
                updated_at, last_id = filters.before
                query = query.where(
                    (Ticket.updated_at < updated_at)
                    | ((Ticket.updated_at == updated_at) & (Ticket.id < last_id))
                )

            rows = session.scalars(
                query.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).limit(limit)
            ).all()
            return [_view(session, row) for row in rows]

    def history(self, ticket_id: uuid.UUID) -> list[TicketStatusHistory]:
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.CONTENT_VIEW)
            require_readable(actor, _load(session, ticket_id))
            return list(
                session.scalars(
                    select(TicketStatusHistory)
                    .where(TicketStatusHistory.ticket_id == ticket_id)
                    .order_by(TicketStatusHistory.created_at.asc())
                )
            )

    # ------------------------------------------------------------- internals

    @staticmethod
    def _find_by_external_ref(session, ref: ExternalRef) -> Ticket | None:
        ticket_id = session.scalar(
            select(TicketExternalRef.ticket_id).where(
                TicketExternalRef.system == ref.system,
                TicketExternalRef.external_id == ref.external_id,
            )
        )
        return session.get(Ticket, ticket_id) if ticket_id else None


def _assignment_event(ticket: Ticket, assignee_id: uuid.UUID) -> NotificationEvent:
    return NotificationEvent(
        recipient_id=assignee_id,
        type=NotificationType.ASSIGNMENT,
        target_type="ticket",
        target_id=ticket.id,
        payload={"key": ticket_key(ticket.number), "title": ticket.title},
    )


def _load(session, ticket_id: uuid.UUID) -> Ticket:
    """Under RLS, so another tenant's ticket is absent rather than forbidden —
    which is MT-6's "a token scoped to tenant A gets 404 for a tenant B
    resource", arriving from the policy instead of from a check."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFound(TICKET_NOT_FOUND)
    return ticket


def require_readable(actor: Principal, ticket: Ticket) -> None:
    """S-21. Public because comments and attachments hang off the same rule.

    ``NotFound``, not ``PermissionDenied``: for a Guest, learning that RL-412
    exists is already more than the decision allows.
    """
    if not can_read_ticket(
        reader=TicketReader(user_id=actor.user_id, role=actor.role),
        assignee_id=ticket.assignee_id,
        reporter_id=ticket.reporter_id,
    ):
        raise NotFound(TICKET_NOT_FOUND)


def _check_rev(ticket: Ticket, expected_rev: int) -> None:
    if ticket.rev != expected_rev:
        # API-3: 409 carrying the current rev, so the client can re-read exactly
        # once rather than poll.
        raise Conflict(REV_MISMATCH, detail={"rev": ticket.rev})


def _check_assignee(session, assignee_id: uuid.UUID | None) -> None:
    if assignee_id is None:
        return
    user = session.get(User, assignee_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise NotFound(ASSIGNEE_NOT_FOUND)


def _check_iteration(session, iteration_id: uuid.UUID | None) -> None:
    if iteration_id is None:
        return
    if session.get(Iteration, iteration_id) is None:
        raise NotFound(ITERATION_NOT_FOUND)


def _check_labels(session, label_ids) -> None:
    for label_id in label_ids:
        if session.get(Label, label_id) is None:
            raise NotFound(LABEL_NOT_FOUND)


def _replace_labels(session, ticket: Ticket, wanted: tuple[uuid.UUID, ...]) -> None:
    current = {
        row.label_id: row
        for row in session.scalars(
            select(TicketLabel).where(TicketLabel.ticket_id == ticket.id)
        )
    }
    for label_id, row in current.items():
        if label_id not in wanted:
            session.delete(row)
    for label_id in wanted:
        if label_id not in current:
            session.add(
                TicketLabel(
                    tenant_id=ticket.tenant_id, ticket_id=ticket.id, label_id=label_id
                )
            )
    session.flush()


def _clean_pr_url(pr_url: str | None) -> str | None:
    """A plain link (TKT-8), but still a link.

    The scheme check is not validation theatre: this value is rendered as an
    anchor, and ``javascript:`` in an href is the cheapest stored-XSS there is.
    """
    if pr_url is None:
        return None
    clean = pr_url.strip()
    if not clean:
        return None
    if not clean.startswith(("http://", "https://")):
        raise ValidationFailed("PR 链接必须以 http:// 或 https:// 开头。")
    return clean


def _blocked_from(session, ticket_id: uuid.UUID) -> TicketStatus | None:
    """Where a Blocked ticket should resume to (§7.2).

    Read from the history row that entered Blocked rather than stored on the
    ticket: one fact, one place. A ``blocked_from`` column would be a second
    copy that can disagree with the history a reviewer is reading.
    """
    return session.scalar(
        select(TicketStatusHistory.from_status)
        .where(
            TicketStatusHistory.ticket_id == ticket_id,
            TicketStatusHistory.to_status == TicketStatus.BLOCKED,
        )
        .order_by(TicketStatusHistory.created_at.desc())
        .limit(1)
    )


def _view(session, ticket: Ticket, *, deduped: bool = False) -> TicketView:
    label_ids = tuple(
        session.scalars(select(TicketLabel.label_id).where(TicketLabel.ticket_id == ticket.id))
    )
    return TicketView(
        id=ticket.id,
        number=ticket.number,
        key=ticket_key(ticket.number),
        type=ticket.type,
        title=ticket.title,
        description=ticket.description,
        status=ticket.status,
        priority=ticket.priority,
        assignee_id=ticket.assignee_id,
        reporter_id=ticket.reporter_id,
        iteration_id=ticket.iteration_id,
        label_ids=label_ids,
        pr_url=ticket.pr_url,
        ai_context=dict(ticket.ai_context or {}),
        rev=ticket.rev,
        submitter=ticket.submitter,
        source=ticket.source,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        deduped=deduped,
    )
