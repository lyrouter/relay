"""TKT-4 · ticket comments and @mentions (design §7.5).

The sentence that shapes this module: **changes made through the API raise
notifications too.** A comment posted by an external system is a comment, and if
it were silent the API would be a back door for editing tickets that nobody is
told about. Nothing here checks how the caller arrived — the ``TenantContext``
records it (``actor_type`` / ``origin``) and the notification goes out either
way.

Mentions resolve by email local part (see :mod:`relay.domain.mentions`). A handle
matching nobody stays plain text: the GH-sync principle — never @ an unrelated
account — is cheaper to hold from the start than to retrofit once someone has
been pinged by a stack trace.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from relay.app import audit, notifications
from relay.app.authz import actor_principal, require
from relay.app.errors import NotFound, ValidationFailed
from relay.app.notifications import NotificationEvent
from relay.app.tickets.service import require_readable
from relay.app.tickets.sharing import TicketReader, can_read_ticket
from relay.context import current_context
from relay.domain.enums import NotificationType, Role, UserStatus
from relay.domain.mentions import MAX_MENTIONS, parse
from relay.domain.permissions import Capability
from relay.domain.tickets import ticket_key
from relay.infra.db.models import Ticket, TicketComment, User
from relay.infra.db.session import tenant_session

BODY_REQUIRED = "评论内容不能为空。"
TICKET_NOT_FOUND = "找不到该工单。"
TOO_MANY_MENTIONS = f"一条评论最多提及 {MAX_MENTIONS} 人。"


@dataclass(frozen=True, slots=True)
class CommentView:
    id: uuid.UUID
    ticket_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str
    created_at: dt.datetime | None
    #: Users actually notified. A handle that matched nobody is not in here and
    #: is not an error — the text simply stays text.
    mentioned: tuple[uuid.UUID, ...] = ()


class CommentService:
    """Runs inside an established ``TenantContext``."""

    def add(
        self, ticket_id: uuid.UUID, body: str, *, now: dt.datetime | None = None
    ) -> CommentView:
        now = now or dt.datetime.now(dt.UTC)
        ctx = current_context()
        clean = (body or "").strip()
        if not clean:
            raise ValidationFailed(BODY_REQUIRED)

        handles = parse(clean)
        if len(handles) > MAX_MENTIONS:
            raise ValidationFailed(TOO_MANY_MENTIONS)

        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.COMMENT_WRITE)

            ticket = session.get(Ticket, ticket_id)
            if ticket is None:
                raise NotFound(TICKET_NOT_FOUND)
            # A Guest has no COMMENT_WRITE, so today this cannot fire. It is here
            # because the day a role gains the capability, "may comment" must not
            # silently become "may comment on any ticket" (S-21).
            require_readable(actor, ticket)

            comment = TicketComment(
                tenant_id=ctx.tenant_id,
                ticket_id=ticket.id,
                author_id=actor.user_id,
                body=clean,
                actor_type=ctx.actor_type,
                origin=ctx.origin,
            )
            session.add(comment)
            session.flush()

            mentioned = _reachable(session, ticket, _resolve(session, handles))
            payload = {
                "key": ticket_key(ticket.number),
                "title": ticket.title,
                "comment_id": str(comment.id),
            }
            notified = []
            for user_id in mentioned:
                if (
                    notifications.emit(
                        session,
                        NotificationEvent(
                            recipient_id=user_id,
                            type=NotificationType.MENTION,
                            # Aggregated per ticket, not per comment: being
                            # mentioned four times in one thread is one thing to
                            # come and read.
                            target_type="ticket",
                            target_id=ticket.id,
                            payload=payload,
                        ),
                        now=now,
                    )
                    is not None
                ):
                    notified.append(user_id)

            audit.record(
                session,
                "ticket.comment_created",
                target_type="ticket",
                target_id=ticket.id,
                after={"comment_id": str(comment.id), "mentions": [str(one) for one in mentioned]},
            )
            view = CommentView(
                id=comment.id,
                ticket_id=ticket.id,
                author_id=comment.author_id,
                body=clean,
                created_at=comment.created_at,
                mentioned=tuple(notified),
            )
            session.commit()
            return view

    def list(self, ticket_id: uuid.UUID, limit: int = 200) -> list[CommentView]:
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.CONTENT_VIEW)
            ticket = session.get(Ticket, ticket_id)
            if ticket is None:
                raise NotFound(TICKET_NOT_FOUND)
            require_readable(actor, ticket)
            rows = session.scalars(
                select(TicketComment)
                .where(TicketComment.ticket_id == ticket_id)
                .order_by(TicketComment.created_at.asc())
                .limit(limit)
            ).all()
            return [
                CommentView(
                    id=row.id,
                    ticket_id=row.ticket_id,
                    author_id=row.author_id,
                    body=row.body,
                    created_at=row.created_at,
                )
                for row in rows
            ]


def _reachable(session, ticket, user_ids: tuple[uuid.UUID, ...]) -> tuple[uuid.UUID, ...]:
    """Drop mentions of people who cannot read the ticket (S-21).

    Only Guests are ever dropped, and only for a ticket that is not theirs. The
    alternative is worse than it sounds: the notification would arrive, the
    inbox would say "you were mentioned on RL-412", and the link would 404 —
    which tells the Guest that RL-412 exists, and tells the author their mention
    worked. Same shape as an unmatched handle: the text stays text.
    """
    if not user_ids:
        return ()
    roles = dict(
        session.execute(select(User.id, User.role).where(User.id.in_(user_ids))).all()
    )
    return tuple(
        user_id
        for user_id in user_ids
        if can_read_ticket(
            reader=TicketReader(user_id=user_id, role=roles.get(user_id, Role.GUEST)),
            assignee_id=ticket.assignee_id,
            reporter_id=ticket.reporter_id,
        )
    )


def _resolve(session, handles: tuple[str, ...]) -> tuple[uuid.UUID, ...]:
    """Turn handles into user ids, under RLS.

    Matched on the local part of the address, which is unique within a tenant
    because domain ↔ tenant is one-to-one (AC-9). Deactivated accounts are
    skipped silently: the text keeps whatever it said, and a departed colleague
    does not accumulate notifications nobody will read (R-2).
    """
    if not handles:
        return ()
    wanted = set(handles)
    local_part = func.split_part(User.email, "@", 1)
    rows = session.execute(
        select(User.id, local_part).where(
            local_part.in_(wanted), User.status == UserStatus.ACTIVE
        )
    ).all()
    by_handle = {handle: user_id for user_id, handle in rows}
    # Ordered by appearance in the comment, not by whatever the database returns.
    return tuple(by_handle[handle] for handle in handles if handle in by_handle)
