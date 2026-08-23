"""AC-4 · the Admin-only account operations (§5.4 row 1).

Role changes, approval of a pending signup, deactivation and reactivation. Four
operations, one guard, and two rules that are not in the design doc because they
only become visible once self-service signup is the primary path:

**A tenant cannot be left without an Admin.** Under invite-only, an
administrator existed because a human made one. With AC-1, accounts arrive on
their own and the only other way to mint an Admin is
``scripts/bootstrap_tenant.py`` — which refuses to add a second Admin to an
existing tenant, precisely so that re-running a deploy is not a takeover. So a
change that would leave zero active Admins is refused here; otherwise the escape
route is an operator editing rows by hand, which is the situation RLS and the
audit log exist to make unnecessary.

**Deactivation ends sessions.** R-2 makes "deactivate in Relay" the only thing
that removes access, since there is no SSO to remove it upstream. A status flag
that leaves live sessions running would satisfy the checklist and not the
requirement.

Every method looks the target up **inside** the tenant session, so a target in
another tenant is not found rather than refused — MT-6's 404-not-403 rule
arriving as a consequence of RLS rather than as a check someone has to remember.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from relay.app import audit
from relay.app.accounts.sessions import SessionService
from relay.app.authz import actor_principal, require
from relay.app.errors import Conflict, NotFound
from relay.domain.enums import Role, UserStatus
from relay.domain.permissions import Capability
from relay.infra.db.models import User
from relay.infra.db.session import tenant_session

USER_NOT_FOUND = "找不到该用户。"
LAST_ADMIN = "这是租户内最后一个管理员，请先指定另一名管理员再操作。"
NOT_PENDING = "该账号不在待审批状态。"


class AdminService:
    """Runs inside an established ``TenantContext`` (see :mod:`relay.context`).

    The context supplies both the tenant and the actor, so neither can be passed
    in wrongly: an Admin cannot act on another tenant, and a call cannot be
    attributed to a user who did not make it.
    """

    def __init__(self, sessions: SessionService | None = None) -> None:
        self._sessions = sessions or SessionService()

    def change_role(self, target_user_id: uuid.UUID, new_role: Role) -> None:
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.USER_MANAGE)
            target = _target(session, target_user_id)

            if target.role is new_role:
                return  # Idempotent, and not worth an audit row.
            if target.role is Role.ADMIN:
                _refuse_if_last_admin(session, target)

            before = target.role
            target.role = new_role
            audit.record(
                session,
                "account.role_changed",
                target_type="user",
                target_id=target.id,
                before={"role": str(before)},
                after={"role": str(new_role)},
            )
            session.commit()

    def approve_pending_user(self, target_user_id: uuid.UUID) -> None:
        """AC-1's ``auto_join=false`` path: an Admin lets a known-domain signup in.

        Approval and email verification are **both** required and neither
        implies the other — an approved account with an unverified address still
        cannot log in (``LoginUseCase`` checks the address first). That is the
        intended shape: approval is the Admin's judgment about the person,
        verification is proof they hold the address the judgment was based on.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.USER_MANAGE)
            target = _target(session, target_user_id)

            if target.status is not UserStatus.PENDING:
                raise Conflict(NOT_PENDING)

            target.status = UserStatus.ACTIVE
            audit.record(
                session,
                "account.approved",
                target_type="user",
                target_id=target.id,
                before={"status": str(UserStatus.PENDING)},
                after={"status": str(UserStatus.ACTIVE)},
            )
            session.commit()

    def deactivate_user(self, target_user_id: uuid.UUID) -> int:
        """Deactivate an account and end its sessions. Returns sessions ended.

        The status change commits *before* the revocation, which is the safe
        order rather than the tidy one. If the revocation then fails, the live
        sessions are still refused on their next request — ``SessionService``
        rejects a session whose user is not ACTIVE and records why — so the
        window is one request wide and closes itself. Reversed, a failed
        deactivation would have already logged the person out of a live account.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.USER_MANAGE)
            target = _target(session, target_user_id)

            if target.status is UserStatus.DEACTIVATED:
                return 0
            if target.role is Role.ADMIN:
                _refuse_if_last_admin(session, target)

            before = target.status
            target.status = UserStatus.DEACTIVATED
            audit.record(
                session,
                "account.deactivated",
                target_type="user",
                target_id=target.id,
                before={"status": str(before)},
                after={"status": str(UserStatus.DEACTIVATED)},
            )
            session.commit()

        return self._sessions.revoke_all_for_user(target_user_id, "deactivated")

    def reactivate_user(self, target_user_id: uuid.UUID) -> None:
        """Bring a deactivated account back.

        No sessions are restored: revocation is not reversible, and the person
        logs in again. Their email verification, however, is untouched — a
        returning colleague does not re-prove an address they already held.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.USER_MANAGE)
            target = _target(session, target_user_id)

            if target.status is not UserStatus.DEACTIVATED:
                raise Conflict("该账号不在停用状态。")

            target.status = UserStatus.ACTIVE
            audit.record(
                session,
                "account.reactivated",
                target_type="user",
                target_id=target.id,
                before={"status": str(UserStatus.DEACTIVATED)},
                after={"status": str(UserStatus.ACTIVE)},
            )
            session.commit()


def _target(session, user_id: uuid.UUID) -> User:
    """Look the target up under RLS.

    ``session.get`` is filtered by the tenant policy, so a user id belonging to
    another tenant simply is not there: the caller gets "no such user" and
    learns nothing about whether the id exists elsewhere (MT-6, §4.5).
    """
    user = session.get(User, user_id)
    if user is None:
        raise NotFound(USER_NOT_FOUND)
    return user


def _refuse_if_last_admin(session, target: User) -> None:
    remaining = session.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.role == Role.ADMIN,
            User.status == UserStatus.ACTIVE,
            User.id != target.id,
        )
    )
    if not remaining:
        raise Conflict(LAST_ADMIN)
