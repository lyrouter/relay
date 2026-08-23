"""AC-5 · team spaces, and the L2 half of AC-4 (§5.4, S-6).

One level, no nesting. That is a decision, not a simplification to be revisited
when someone asks for sub-teams: the space's only job is to name the L2 sharing
scope, and a tree turns "who can read this" into a question with a recursive
answer that nobody can check by looking.

The rule this module exists to enforce mechanically is **S-6: joining a space
does not grant a Guest L2.** :meth:`SpaceService.grants_space_read` checks the
role *before* the membership, so that a Guest who is a member of the space still
gets False — and adding "the contractor" to the team space does not silently
hand over every space-shared log in it. LOG-6 composes this; it must not
reimplement it.

Space *creation* is an Admin power (see ``ROLE_CAPABILITIES``), because creating
a space and filling it with people is granting read access. Running one is not:
a space owner manages that space's membership, which is a per-object check here
rather than a role.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from relay.app import audit
from relay.app.authz import Principal, actor_principal, require
from relay.app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from relay.context import current_context
from relay.domain.enums import Role, ShareLevel, SpaceRole, UserStatus
from relay.domain.permissions import Capability, role_reaches_share_level
from relay.infra.db.models import Space, SpaceMember, User
from relay.infra.db.session import tenant_session

SPACE_NOT_FOUND = "找不到该空间。"
USER_NOT_FOUND = "找不到该用户。"
NAME_TAKEN = "同名空间已存在。"
NOT_SPACE_ADMIN = "只有管理员或该空间的所有者可以调整成员。"
LAST_OWNER = "这是该空间最后一位所有者，请先指定另一位所有者。"


class SpaceService:
    """Runs inside an established ``TenantContext``."""

    def create(self, name: str, description: str = "") -> uuid.UUID:
        """Create a space. The creator becomes its first owner.

        Not a separate "assign owner" step: a space with no owner is a space
        whose membership only an Admin can change, which is the opposite of what
        a team space is for.
        """
        clean = name.strip()
        if not clean:
            raise ValidationFailed("空间名称不能为空。")

        ctx = current_context()
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.SPACE_MANAGE)

            # Checked here for the message; the database still holds the line
            # with UNIQUE (tenant_id, name) if two requests race.
            if session.scalar(select(Space.id).where(Space.name == clean)) is not None:
                raise Conflict(NAME_TAKEN)

            space = Space(tenant_id=ctx.tenant_id, name=clean, description=description.strip())
            session.add(space)
            session.flush()
            session.add(
                SpaceMember(
                    tenant_id=ctx.tenant_id,
                    space_id=space.id,
                    user_id=actor.user_id,
                    space_role=SpaceRole.OWNER,
                )
            )
            audit.record(
                session,
                "space.created",
                target_type="space",
                target_id=space.id,
                after={"name": clean, "owner": str(actor.user_id)},
            )
            session.commit()
            return space.id

    def add_member(
        self,
        space_id: uuid.UUID,
        user_id: uuid.UUID,
        space_role: SpaceRole = SpaceRole.MEMBER,
    ) -> None:
        """Add or re-role a member. Idempotent for an unchanged role.

        A Guest may be added — S-6 is about what membership *grants*, not about
        who may hold it, and refusing the membership would make "show the
        contractor the two documents they need" impossible to express.
        """
        ctx = current_context()
        with tenant_session() as session:
            actor = actor_principal(session)
            space = _space(session, space_id)
            _require_space_admin(session, actor, space.id)

            target = session.get(User, user_id)
            if target is None or target.status is UserStatus.DEACTIVATED:
                # Same answer for "not in this tenant" and "deactivated": the
                # caller has no business distinguishing them, and RLS has
                # already made the cross-tenant case indistinguishable anyway.
                raise NotFound(USER_NOT_FOUND)

            existing = session.scalars(
                select(SpaceMember).where(
                    SpaceMember.space_id == space.id, SpaceMember.user_id == user_id
                )
            ).first()
            if existing is not None:
                if existing.space_role is space_role:
                    return
                if existing.space_role is SpaceRole.OWNER:
                    _refuse_if_last_owner(session, space.id, user_id)
                before = existing.space_role
                existing.space_role = space_role
                audit.record(
                    session,
                    "space.member_role_changed",
                    target_type="space",
                    target_id=space.id,
                    before={"user": str(user_id), "space_role": str(before)},
                    after={"user": str(user_id), "space_role": str(space_role)},
                )
                session.commit()
                return

            session.add(
                SpaceMember(
                    tenant_id=ctx.tenant_id,
                    space_id=space.id,
                    user_id=user_id,
                    space_role=space_role,
                )
            )
            audit.record(
                session,
                "space.member_added",
                target_type="space",
                target_id=space.id,
                after={"user": str(user_id), "space_role": str(space_role)},
            )
            session.commit()

    def remove_member(self, space_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Remove a member. Removing the last owner is refused.

        Note what this does *not* do: it does not touch L1 grants. Someone
        removed from a space loses L2 access to its logs and keeps anything
        shared with them by name, which is the difference between the two levels.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            space = _space(session, space_id)
            _require_space_admin(session, actor, space.id)

            member = session.scalars(
                select(SpaceMember).where(
                    SpaceMember.space_id == space.id, SpaceMember.user_id == user_id
                )
            ).first()
            if member is None:
                raise NotFound("该用户不是该空间的成员。")
            if member.space_role is SpaceRole.OWNER:
                _refuse_if_last_owner(session, space.id, user_id)

            session.delete(member)
            audit.record(
                session,
                "space.member_removed",
                target_type="space",
                target_id=space.id,
                before={"user": str(user_id), "space_role": str(member.space_role)},
            )
            session.commit()

    def member_ids(self, space_id: uuid.UUID) -> list[uuid.UUID]:
        """Who is in this space. No capability gate beyond being an active user.

        Deliberate: within a tenant, who is on which team is not a secret, and
        the alternative — only members may see the membership — makes "ask to be
        added" a question nobody can direct at anyone. RLS still bounds it to the
        tenant, and a space id from another tenant is not found.
        """
        with tenant_session() as session:
            actor_principal(session)
            space = _space(session, space_id)
            return list(
                session.scalars(
                    select(SpaceMember.user_id).where(SpaceMember.space_id == space.id)
                )
            )

    def space_ids_for(self, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        """Which spaces a user belongs to.

        An internal query for sharing evaluation (LOG-6), not an endpoint: it is
        tenant-scoped by RLS and returns ids only. Whether a *log* shared into
        one of those spaces is readable is :meth:`grants_space_read`, which is
        where the role rule lives.
        """
        with tenant_session() as session:
            return frozenset(
                session.scalars(select(SpaceMember.space_id).where(SpaceMember.user_id == user_id))
            )

    def grants_space_read(
        self, space_id: uuid.UUID, reader_id: uuid.UUID, reader_role: Role
    ) -> bool:
        """The L2 decision, role first (S-6).

        Order is the substance of this function. Checking membership first and
        the role second would produce the same answer today and would be one
        careless edit away from "a Guest in the space can read it" — the exact
        thing S-6 rules out. A Guest gets False here without a query.
        """
        if not role_reaches_share_level(reader_role, ShareLevel.SPACE):
            return False
        with tenant_session() as session:
            return (
                session.scalar(
                    select(SpaceMember.id).where(
                        SpaceMember.space_id == space_id, SpaceMember.user_id == reader_id
                    )
                )
                is not None
            )


def _space(session, space_id: uuid.UUID) -> Space:
    space = session.get(Space, space_id)
    if space is None:
        raise NotFound(SPACE_NOT_FOUND)
    return space


def _require_space_admin(session, actor: Principal, space_id: uuid.UUID) -> None:
    """Admin, or an owner of this space.

    The per-object half of AC-4: ``SPACE_MANAGE`` is the role answer, and space
    ownership is the object answer. Neither is expressible as the other, which
    is why both are here rather than a fourth role.
    """
    if actor.can(Capability.SPACE_MANAGE):
        return
    owner = session.scalar(
        select(SpaceMember.id).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == actor.user_id,
            SpaceMember.space_role == SpaceRole.OWNER,
        )
    )
    if owner is None:
        raise PermissionDenied(NOT_SPACE_ADMIN)


def _refuse_if_last_owner(session, space_id: uuid.UUID, user_id: uuid.UUID) -> None:
    remaining = session.scalar(
        select(func.count())
        .select_from(SpaceMember)
        .where(
            SpaceMember.space_id == space_id,
            SpaceMember.space_role == SpaceRole.OWNER,
            SpaceMember.user_id != user_id,
        )
    )
    if not remaining:
        raise Conflict(LAST_OWNER)
