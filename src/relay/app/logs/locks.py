"""LOG-4 · the edit lock (decided, S-7).

Real-time collaborative editing is **not** in S1: a CRDT is expensive and the
team is small. The replacement is a lock with a conflict prompt — TTL 5 minutes,
renewed by a heartbeat, and after it lapses somebody else may take over.

The promise attached to that decision is the important part: **unsaved content is
saved as a version, never discarded.** Note what delivers it — autosave writes a
version (see :meth:`relay.app.logs.service.LogService.save`), so by the time a
lock lapses the previous editor's work *is already* version N. There is nothing
for the takeover path to rescue, which is why there is no rescue path here to get
wrong. What :meth:`EditLockService.acquire` does instead is report the version
number, so the UI can say "上一位编辑者的草稿已另存为版本 N" truthfully.

A lock is a courtesy, not a permission: it stops two people typing over each
other, and it is not an access control. Anyone who may edit the log may take a
lapsed lock, and nobody can use a lock to hold a document hostage — five minutes
of silence is all it survives.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app.authz import actor_principal, require
from relay.app.errors import Conflict, NotFound, PermissionDenied
from relay.domain.enums import Role
from relay.domain.permissions import Capability
from relay.infra.db.models import Log, LogEditLock, User
from relay.infra.db.session import tenant_session

#: S-7. Short enough that a closed laptop frees the document inside a coffee
#: break, long enough to survive a heartbeat that missed a beat.
LOCK_TTL = dt.timedelta(minutes=5)

LOCK_HELD = "该日志正在被 {holder} 编辑，{minutes} 分钟后可接管。"
NOT_THE_HOLDER = "编辑锁不在你手上，请重新获取。"
LOG_NOT_FOUND = "找不到该日志。"


@dataclass(frozen=True, slots=True)
class LockView:
    log_id: uuid.UUID
    holder_id: uuid.UUID
    expires_at: dt.datetime
    #: Set when this acquire took a lapsed lock from somebody else. The UI shows
    #: it together with ``last_saved_version``.
    taken_over_from: uuid.UUID | None = None
    #: The version their work is safe in — the sentence S-7 asks the UI to show.
    last_saved_version: int | None = None


class EditLockService:
    """Runs inside an established ``TenantContext``."""

    def acquire(self, log_id: uuid.UUID, *, now: dt.datetime | None = None) -> LockView:
        """Take the edit lock, or say who has it and for how long.

        Re-acquiring your own live lock renews it rather than failing: a browser
        that reloads mid-edit should not have to wait out its own TTL.
        """
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            log = _load(session, log_id)
            _require_author(actor, log)

            existing = session.scalars(
                select(LogEditLock).where(LogEditLock.log_id == log.id).with_for_update()
            ).first()

            if existing is not None and existing.expires_at > now:
                if existing.holder_id != actor.user_id:
                    holder = session.get(User, existing.holder_id)
                    remaining = int((existing.expires_at - now).total_seconds() // 60) + 1
                    raise Conflict(
                        LOCK_HELD.format(
                            holder=(holder.display_name if holder else "其他用户"),
                            minutes=remaining,
                        ),
                        detail={
                            "holder_id": str(existing.holder_id),
                            "expires_at": existing.expires_at.isoformat(),
                        },
                    )
                existing.expires_at = now + LOCK_TTL
                view = LockView(log.id, actor.user_id, existing.expires_at)
                session.commit()
                return view

            previous = existing.holder_id if existing is not None else None
            if existing is not None:
                session.delete(existing)
                session.flush()

            lock = LogEditLock(
                tenant_id=log.tenant_id,
                log_id=log.id,
                holder_id=actor.user_id,
                acquired_at=now,
                expires_at=now + LOCK_TTL,
            )
            session.add(lock)
            view = LockView(
                log_id=log.id,
                holder_id=actor.user_id,
                expires_at=lock.expires_at,
                taken_over_from=previous if previous != actor.user_id else None,
                # Autosave already wrote it. Reported so the takeover message is
                # a fact rather than a reassurance.
                last_saved_version=log.current_version,
            )
            session.commit()
            return view

    def heartbeat(self, log_id: uuid.UUID, *, now: dt.datetime | None = None) -> LockView:
        """Renew a lock you hold.

        Refuses once it has lapsed, even if nobody else has taken it: silently
        renewing an expired lock would mean a second editor could already be
        typing, and the first one would never be told.
        """
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            lock = self._live_lock(session, log_id, now)
            if lock is None or lock.holder_id != actor.user_id:
                raise PermissionDenied(NOT_THE_HOLDER)
            lock.expires_at = now + LOCK_TTL
            view = LockView(log_id, actor.user_id, lock.expires_at)
            session.commit()
            return view

    def release(self, log_id: uuid.UUID, *, now: dt.datetime | None = None) -> bool:
        """Give the lock up. Returns whether there was one of yours to give up."""
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            lock = session.scalars(
                select(LogEditLock).where(LogEditLock.log_id == log_id).with_for_update()
            ).first()
            if lock is None or lock.holder_id != actor.user_id:
                return False
            session.delete(lock)
            session.commit()
            return True

    def holder(self, log_id: uuid.UUID, *, now: dt.datetime | None = None) -> LockView | None:
        """Who holds a live lock, if anyone. A lapsed lock reads as free."""
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor_principal(session)
            lock = self._live_lock(session, log_id, now)
            if lock is None:
                return None
            return LockView(lock.log_id, lock.holder_id, lock.expires_at)

    @staticmethod
    def _live_lock(session, log_id: uuid.UUID, now: dt.datetime) -> LogEditLock | None:
        lock = session.scalars(
            select(LogEditLock).where(LogEditLock.log_id == log_id).with_for_update()
        ).first()
        if lock is None or lock.expires_at <= now:
            return None
        return lock


def _load(session, log_id: uuid.UUID) -> Log:
    log = session.get(Log, log_id)
    if log is None:
        raise NotFound(LOG_NOT_FOUND)
    return log


def _require_author(actor, log: Log) -> None:
    if actor.user_id == log.author_id or actor.role is Role.ADMIN:
        return
    raise PermissionDenied("只能编辑自己创建的日志。")
