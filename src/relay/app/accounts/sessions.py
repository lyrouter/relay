"""Session resolution and revocation (AC-2).

The read side of ``UserSession``: turning a bearer token back into a
``TenantContext``, and ending sessions when they should end.

Revocation is not an afterthought here. Without SSO, R-2 makes "deactivate in
Relay" the only thing that removes access, so ``revoke_all_for_user`` is the
mechanism that requirement actually rests on.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app.accounts.login import IDLE_TIMEOUT
from relay.app.errors import ApplicationError
from relay.context import ActorType, Origin, TenantContext
from relay.domain.enums import UserStatus
from relay.infra.db.models import User, UserSession
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.db.session import commit_and_raise
from relay.infra.security.tokens import hash_token

#: One message for missing, revoked, idled out and aged out.
SESSION_INVALID = "登录状态已失效，请重新登录。"


class SessionExpired(ApplicationError):
    code = "session_expired"


#: AC-3. Distinct from ``SessionExpired`` on purpose: the caller is
#: authenticated and the fix is a six-digit code, not another password. A UI that
#: cannot tell the two apart sends the user back to the login form, where they
#: will type the password that already worked.
MFA_OUTSTANDING = "请输入两步验证动态码后继续。"


class MfaNotSatisfied(ApplicationError):
    code = "mfa_required"


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    session_id: uuid.UUID
    context: TenantContext
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    mfa_satisfied: bool


class SessionService:
    def __init__(self) -> None:
        self._pre = PreTenantRepository()

    def resolve(
        self, token: str, *, origin: Origin = Origin.WEB, now: dt.datetime | None = None
    ) -> ResolvedSession:
        """Validate a session token and slide the idle window.

        Runs on the pre-tenant connection for the same reason signup does: the
        token is all the caller has, so the tenant is not known until it
        resolves. Everything downstream then runs under the resulting context.
        """
        now = now or dt.datetime.now(dt.UTC)
        with self._pre.session() as session:
            record = session.scalars(
                select(UserSession).where(UserSession.token_hash == hash_token(token.strip()))
            ).first()

            # One message for missing, revoked, idled out and aged out. A caller
            # learning which one applies learns whether the token was ever real.
            if record is None or record.revoked_at is not None:
                raise SessionExpired(SESSION_INVALID)
            # commit_and_raise on each of these: `_end` writes revoked_at and
            # revoked_reason, and raising past the context manager would discard
            # both. The session would still be refused — expiry is recomputed
            # every time — but the row would stay un-revoked forever and
            # revoked_reason, which exists for exactly the investigation where
            # guessing is expensive, would never hold anything.
            if record.idle_expires_at <= now:
                self._end(record, now, "idle")
                commit_and_raise(session, SessionExpired(SESSION_INVALID))
            if record.absolute_expires_at <= now:
                self._end(record, now, "absolute")
                commit_and_raise(session, SessionExpired(SESSION_INVALID))

            user = session.get(User, record.user_id)
            if user is None or user.status is not UserStatus.ACTIVE:
                # Catches the account deactivated *after* this session opened —
                # which is exactly the R-2 offboarding case.
                self._end(record, now, "deactivated")
                commit_and_raise(session, SessionExpired(SESSION_INVALID))

            record.last_seen_at = now
            # Slide, but never past the absolute deadline.
            record.idle_expires_at = min(now + IDLE_TIMEOUT, record.absolute_expires_at)
            session.flush()

            return ResolvedSession(
                session_id=record.id,
                context=TenantContext(
                    tenant_id=record.tenant_id,
                    actor_id=record.user_id,
                    actor_type=ActorType.USER,
                    origin=origin,
                ),
                user_id=record.user_id,
                tenant_id=record.tenant_id,
                mfa_satisfied=record.mfa_satisfied,
            )

    def logout(self, token: str, *, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.UTC)
        with self._pre.session() as session:
            record = session.scalars(
                select(UserSession).where(UserSession.token_hash == hash_token(token.strip()))
            ).first()
            if record and record.revoked_at is None:
                self._end(record, now, "logout")
                session.flush()

    def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        reason: str,
        *,
        except_session_id: uuid.UUID | None = None,
        now: dt.datetime | None = None,
    ) -> int:
        """End every live session for a user. Returns how many.

        Called on password change, on deactivation, and by an Admin. The
        ``except_session_id`` escape is for "change your password and stay
        logged in here", which is the only case where keeping one alive is
        right.
        """
        now = now or dt.datetime.now(dt.UTC)
        with self._pre.session() as session:
            live = session.scalars(
                select(UserSession).where(
                    UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
                )
            ).all()
            ended = 0
            for record in live:
                if except_session_id and record.id == except_session_id:
                    continue
                self._end(record, now, reason)
                ended += 1
            session.flush()
            return ended

    @staticmethod
    def _end(record: UserSession, now: dt.datetime, reason: str) -> None:
        record.revoked_at = now
        record.revoked_reason = reason
