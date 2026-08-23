"""TKT-8 · labels and iterations — the board's taxonomy.

Both are tenant-wide vocabulary rather than per-ticket data, so they live here
instead of on :class:`~relay.app.tickets.service.TicketService`: attaching a
label to a ticket is editing that ticket, but *inventing* the label is changing
what the whole team can say about every ticket.

**Neither can be deleted, deliberately.** Deleting a label detaches it from
every ticket that carried it, which silently rewrites the history the board is
read from — and there is no undo, because ``ticket_label`` rows are gone rather
than marked. An iteration *closes* (the flag exists on the model) and a label
that fell out of use costs a row. If a real need for deletion turns up, it wants
an archive flag and a migration, not a DELETE added here.

Creating and renaming need ``TICKET_WRITE``, not an Admin: a Member running a
project has to be able to open next sprint's iteration without filing a request.
The destructive operation is the one that would have needed a role, and it does
not exist.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app import audit
from relay.app.authz import actor_principal, require
from relay.app.errors import Conflict, NotFound, ValidationFailed
from relay.context import current_context
from relay.domain.permissions import Capability
from relay.infra.db.models import Iteration, Label
from relay.infra.db.session import tenant_session

LABEL_NAME_TAKEN = "同名标签已存在。"
ITERATION_NAME_TAKEN = "同名迭代已存在。"
LABEL_NOT_FOUND = "找不到该标签。"
ITERATION_NOT_FOUND = "找不到该迭代。"

#: Hex, three or six digits. Rendered into a style attribute, so an unvalidated
#: value is a CSS injection rather than a cosmetic problem.
_HEX_LENGTHS = (4, 7)


@dataclass(frozen=True, slots=True)
class LabelView:
    id: uuid.UUID
    name: str
    color: str


@dataclass(frozen=True, slots=True)
class IterationView:
    id: uuid.UUID
    name: str
    starts_on: dt.date | None
    ends_on: dt.date | None
    closed: bool


class BoardMetadataService:
    """Runs inside an established ``TenantContext``."""

    # ---------------------------------------------------------------- labels

    def create_label(self, name: str, color: str = "#6b7280") -> LabelView:
        clean = _clean_name(name, "标签名称")
        checked = _check_color(color)
        ctx = current_context()
        with tenant_session() as session:
            require(actor_principal(session), Capability.TICKET_WRITE)
            if session.scalar(select(Label.id).where(Label.name == clean)) is not None:
                raise Conflict(LABEL_NAME_TAKEN)
            label = Label(tenant_id=ctx.tenant_id, name=clean, color=checked)
            session.add(label)
            session.flush()
            audit.record(
                session,
                "label.created",
                target_type="label",
                target_id=label.id,
                after={"name": clean},
            )
            view = LabelView(label.id, label.name, label.color)
            session.commit()
            return view

    def rename_label(
        self, label_id: uuid.UUID, name: str | None = None, color: str | None = None
    ) -> LabelView:
        with tenant_session() as session:
            require(actor_principal(session), Capability.TICKET_WRITE)
            label = session.get(Label, label_id)
            if label is None:
                raise NotFound(LABEL_NOT_FOUND)

            before = {"name": label.name, "color": label.color}
            if name is not None:
                clean = _clean_name(name, "标签名称")
                if clean != label.name and (
                    session.scalar(select(Label.id).where(Label.name == clean)) is not None
                ):
                    raise Conflict(LABEL_NAME_TAKEN)
                label.name = clean
            if color is not None:
                label.color = _check_color(color)

            audit.record(
                session,
                "label.updated",
                target_type="label",
                target_id=label.id,
                before=before,
                after={"name": label.name, "color": label.color},
            )
            view = LabelView(label.id, label.name, label.color)
            session.commit()
            return view

    def labels(self) -> list[LabelView]:
        with tenant_session() as session:
            require(actor_principal(session), Capability.CONTENT_VIEW)
            return [
                LabelView(row.id, row.name, row.color)
                for row in session.scalars(select(Label).order_by(Label.name))
            ]

    # ------------------------------------------------------------ iterations

    def create_iteration(
        self,
        name: str,
        starts_on: dt.date | None = None,
        ends_on: dt.date | None = None,
    ) -> IterationView:
        clean = _clean_name(name, "迭代名称")
        if starts_on and ends_on and ends_on < starts_on:
            raise ValidationFailed("迭代结束日期不能早于开始日期。")
        ctx = current_context()
        with tenant_session() as session:
            require(actor_principal(session), Capability.TICKET_WRITE)
            if session.scalar(select(Iteration.id).where(Iteration.name == clean)) is not None:
                raise Conflict(ITERATION_NAME_TAKEN)
            iteration = Iteration(
                tenant_id=ctx.tenant_id, name=clean, starts_on=starts_on, ends_on=ends_on
            )
            session.add(iteration)
            session.flush()
            audit.record(
                session,
                "iteration.created",
                target_type="iteration",
                target_id=iteration.id,
                after={"name": clean},
            )
            view = _iteration_view(iteration)
            session.commit()
            return view

    def set_iteration_closed(self, iteration_id: uuid.UUID, closed: bool) -> IterationView:
        """Close or reopen an iteration.

        Closing does **not** touch the tickets in it. A closed iteration is a
        statement about the sprint, not about the work — moving unfinished
        tickets is a decision somebody makes ticket by ticket, and doing it
        automatically here would rewrite an assignee's board overnight.
        """
        with tenant_session() as session:
            require(actor_principal(session), Capability.TICKET_WRITE)
            iteration = session.get(Iteration, iteration_id)
            if iteration is None:
                raise NotFound(ITERATION_NOT_FOUND)
            if iteration.closed == closed:
                return _iteration_view(iteration)

            iteration.closed = closed
            audit.record(
                session,
                "iteration.closed" if closed else "iteration.reopened",
                target_type="iteration",
                target_id=iteration.id,
                before={"closed": not closed},
                after={"closed": closed},
            )
            view = _iteration_view(iteration)
            session.commit()
            return view

    def iterations(self, *, include_closed: bool = True) -> list[IterationView]:
        with tenant_session() as session:
            require(actor_principal(session), Capability.CONTENT_VIEW)
            query = select(Iteration).order_by(
                Iteration.starts_on.desc().nullslast(), Iteration.name
            )
            if not include_closed:
                query = query.where(Iteration.closed.is_(False))
            return [_iteration_view(row) for row in session.scalars(query)]


def _iteration_view(iteration: Iteration) -> IterationView:
    return IterationView(
        id=iteration.id,
        name=iteration.name,
        starts_on=iteration.starts_on,
        ends_on=iteration.ends_on,
        closed=iteration.closed,
    )


def _clean_name(name: str, what: str) -> str:
    clean = (name or "").strip()
    if not clean:
        raise ValidationFailed(f"{what}不能为空。")
    return clean


def _check_color(color: str) -> str:
    clean = (color or "").strip().lower()
    if (
        not clean.startswith("#")
        or len(clean) not in _HEX_LENGTHS
        or not all(char in "0123456789abcdef" for char in clean[1:])
    ):
        raise ValidationFailed("标签颜色必须是 #rgb 或 #rrggbb 形式的十六进制值。")
    return clean
