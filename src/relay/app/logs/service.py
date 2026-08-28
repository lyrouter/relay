"""LOG-4 / LOG-6 / LOG-9 · authoring, versions, sharing, the knowledge marker.

Three decisions shape this module:

* **Autosave writes a version.** That is what makes S-7's promise — "unsaved
  content is saved as a version, never discarded" — true by construction rather
  than by a rescue step at lock-takeover time. There is nothing to rescue,
  because the last autosave is already version N. Identical consecutive saves
  are skipped so that an idle editor does not mint a version a second.
* **Rollback appends.** §6.2: rollback creates a *new version from old content*;
  history is never rewritten. So there is no path that deletes or edits a
  version row, and ``rolled_back_from`` records where the content came from.
* **Editing a log means editing your own.** §5.4 gives Member "日志创建 / 编辑
  自己的", so authorship is checked alongside the capability — with Admin as the
  exception §6.3 already establishes for reading.

The read path goes through :func:`relay.app.logs.sharing.can_read`, never through
a hand-rolled level check. The list query mirrors that same rule in SQL, and
``test_logs.py`` asserts the two agree — two implementations of one rule is the
failure mode worth spending a test on.

**Reads can write** (S-19). When an Admin reads a log that only their role let
them reach, :mod:`relay.app.logs.read_audit` adds a row and the read path
commits. Nothing else is recorded — a Member reading their own space's logs
leaves no trace — so the common path is still a pure read with nothing to flush.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from relay.app import audit
from relay.app.authz import Principal, actor_principal, require
from relay.app.errors import NotFound, PayloadTooLarge, PermissionDenied, ValidationFailed
from relay.app.logs import read_audit
from relay.app.logs.sharing import Reader, can_read
from relay.domain.diffs import DiffLine, line_diff
from relay.domain.enums import LogFormat, Role, ShareLevel, UserStatus
from relay.domain.note_import import MAX_BYTES, NoteFileTooLarge, UnsupportedNoteFile, parse_note
from relay.domain.permissions import Capability
from relay.infra.db.models import Log, LogShareGrant, LogVersion, SpaceMember, User
from relay.infra.db.session import tenant_session
from relay.infra.db.visibility import visible_logs_predicate

LOG_NOT_FOUND = "找不到该日志。"
TITLE_REQUIRED = "标题不能为空。"
NOT_THE_AUTHOR = "只能编辑自己创建的日志。"
GRANTEE_NOT_FOUND = "找不到要授权的用户。"
SPACE_REQUIRED = "分享到空间前，需要先把日志放进一个空间。"
VERSION_NOT_FOUND = "找不到该版本。"

#: LOG-9 · S-16. "Checked **and** body ≥ 300 characters" counts automatically
#: toward the acceptance metric; ten are spot-checked by hand before acceptance.
#: The threshold lives here so INT-8's dashboard and this code cannot disagree.
KNOWLEDGE_MIN_BODY = 300

UNSET: Any = object()


@dataclass(frozen=True, slots=True)
class LogView:
    id: uuid.UUID
    title: str
    body: str
    format: LogFormat
    share_level: ShareLevel
    space_id: uuid.UUID | None
    author_id: uuid.UUID
    current_version: int
    knowledge_candidate: bool
    marked_by: uuid.UUID | None
    marked_at: dt.datetime | None
    updated_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class VersionInfo:
    version_no: int
    title: str
    author_id: uuid.UUID
    created_at: dt.datetime | None
    rolled_back_from: int | None


class LogService:
    """Runs inside an established ``TenantContext``."""

    # ---------------------------------------------------------------- create

    def create(
        self,
        title: str,
        body: str = "",
        *,
        format: LogFormat = LogFormat.MARKDOWN,
        space_id: uuid.UUID | None = None,
        share_level: ShareLevel = ShareLevel.PRIVATE,
        now: dt.datetime | None = None,
    ) -> LogView:
        """Create a log at version 1. Default share level is **private**.

        Defaulting to L0 rather than L2 or L3 is deliberate: a draft that starts
        visible is a draft somebody reads mid-thought, and the cost of the wrong
        default runs one way only.
        """
        now = now or dt.datetime.now(dt.UTC)
        clean = title.strip()
        if not clean:
            raise ValidationFailed(TITLE_REQUIRED)

        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            _check_space(share_level, space_id)

            log = Log(
                tenant_id=actor.tenant_id,
                space_id=space_id,
                author_id=actor.user_id,
                title=clean,
                body=body,
                format=format,
                share_level=share_level,
                current_version=1,
            )
            session.add(log)
            session.flush()
            session.add(
                LogVersion(
                    tenant_id=actor.tenant_id,
                    log_id=log.id,
                    version_no=1,
                    title=clean,
                    body=body,
                    author_id=actor.user_id,
                )
            )
            audit.record(
                session,
                "log.created",
                target_type="log",
                target_id=log.id,
                after={"title": clean, "share_level": str(share_level)},
            )
            view = _view(log)
            session.commit()
            return view

    def import_note(self, filename: str, data: bytes, *, now: dt.datetime | None = None) -> LogView:
        """Create a log from an uploaded Markdown or HTML file.

        Lands as Markdown with the knowledge marker already on: the author is
        on 知识, importing into it, and asking them to tick 「加入知识库」 on
        every file would make the import a two-step that nobody finishes. Share
        level stays private — importing is not publishing.
        """
        now = now or dt.datetime.now(dt.UTC)
        if len(data) > MAX_BYTES:
            raise PayloadTooLarge(f"文件不能超过 {MAX_BYTES // (1024 * 1024)} MB。")
        try:
            note = parse_note(filename, data)
        except NoteFileTooLarge as exc:
            raise PayloadTooLarge(str(exc)) from exc
        except UnsupportedNoteFile as exc:
            raise ValidationFailed(str(exc)) from exc

        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)

            log = Log(
                tenant_id=actor.tenant_id,
                space_id=None,
                author_id=actor.user_id,
                title=note.title,
                body=note.body,
                format=LogFormat.MARKDOWN,
                share_level=ShareLevel.PRIVATE,
                current_version=1,
                knowledge_candidate=True,
                marked_by=actor.user_id,
                marked_at=now,
            )
            session.add(log)
            session.flush()
            session.add(
                LogVersion(
                    tenant_id=actor.tenant_id,
                    log_id=log.id,
                    version_no=1,
                    title=note.title,
                    body=note.body,
                    author_id=actor.user_id,
                )
            )
            audit.record(
                session,
                "log.imported",
                target_type="log",
                target_id=log.id,
                after={
                    "title": note.title,
                    "filename": filename,
                    "source": note.source,
                    "knowledge_candidate": True,
                },
            )
            view = _view(log)
            session.commit()
            return view

    # ------------------------------------------------------------------ save

    def save(
        self,
        log_id: uuid.UUID,
        *,
        title: Any = UNSET,
        body: Any = UNSET,
        now: dt.datetime | None = None,
    ) -> LogView:
        """Save (or autosave) a log, appending a version if anything changed.

        Returns the log either way. An unchanged save is a no-op rather than an
        error: autosave fires on a timer, and most of those timers fire on text
        nobody touched.
        """
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            log = _load(session, log_id)
            _require_author(actor, log)

            new_title = log.title if title is UNSET else (title or "").strip()
            new_body = log.body if body is UNSET else (body or "")
            if not new_title:
                raise ValidationFailed(TITLE_REQUIRED)

            if new_title == log.title and new_body == log.body:
                return _view(log)

            log.title = new_title
            log.body = new_body
            log.current_version += 1
            session.add(
                LogVersion(
                    tenant_id=log.tenant_id,
                    log_id=log.id,
                    version_no=log.current_version,
                    title=new_title,
                    body=new_body,
                    author_id=actor.user_id,
                )
            )
            view = _view(log)
            session.commit()
            return view

    # -------------------------------------------------------------- versions

    def versions(self, log_id: uuid.UUID) -> list[VersionInfo]:
        """Newest first. Bodies are not included — a history list of fifty
        versions would otherwise ship fifty documents to render ten dates."""
        with tenant_session() as session:
            actor = actor_principal(session)
            log = _load(session, log_id)
            audited = _require_readable(session, actor, log, via="versions")
            rows = session.scalars(
                select(LogVersion)
                .where(LogVersion.log_id == log.id)
                .order_by(LogVersion.version_no.desc())
            ).all()
            infos = [
                VersionInfo(
                    version_no=row.version_no,
                    title=row.title,
                    author_id=row.author_id,
                    created_at=row.created_at,
                    rolled_back_from=row.rolled_back_from,
                )
                for row in rows
            ]
            if audited:
                session.commit()
            return infos

    def diff(self, log_id: uuid.UUID, from_version: int, to_version: int) -> tuple[DiffLine, ...]:
        with tenant_session() as session:
            actor = actor_principal(session)
            log = _load(session, log_id)
            audited = _require_readable(session, actor, log, via="diff")
            old = _version(session, log.id, from_version)
            new = _version(session, log.id, to_version)
            lines = line_diff(old.body, new.body)
            if audited:
                session.commit()
            return lines

    def rollback(
        self, log_id: uuid.UUID, to_version: int, *, now: dt.datetime | None = None
    ) -> LogView:
        """Roll back by **appending** a version holding the old content (§6.2).

        History is never rewritten, so a rollback is auditable and itself
        reversible. ``rolled_back_from`` records where the content came from —
        without it, a rollback is indistinguishable from somebody retyping an
        old draft.
        """
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            log = _load(session, log_id)
            _require_author(actor, log)
            source = _version(session, log.id, to_version)

            log.title = source.title
            log.body = source.body
            log.current_version += 1
            session.add(
                LogVersion(
                    tenant_id=log.tenant_id,
                    log_id=log.id,
                    version_no=log.current_version,
                    title=source.title,
                    body=source.body,
                    author_id=actor.user_id,
                    rolled_back_from=to_version,
                )
            )
            audit.record(
                session,
                "log.rolled_back",
                target_type="log",
                target_id=log.id,
                before={"version": log.current_version - 1},
                after={"version": log.current_version, "from_version": to_version},
            )
            view = _view(log)
            session.commit()
            return view

    # --------------------------------------------------------------- sharing

    def set_share_level(
        self,
        log_id: uuid.UUID,
        share_level: ShareLevel,
        *,
        space_id: Any = UNSET,
    ) -> LogView:
        """Change who can read a log. Author or Admin.

        Widening the level is an access grant, so it is audited like one — this
        is the row somebody reads when asking how a document got out.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            log = _load(session, log_id)
            _require_author(actor, log)

            target_space = log.space_id if space_id is UNSET else space_id
            _check_space(share_level, target_space)

            before = log.share_level
            log.share_level = share_level
            log.space_id = target_space
            audit.record(
                session,
                "log.share_level_changed",
                target_type="log",
                target_id=log.id,
                before={"share_level": str(before)},
                after={"share_level": str(share_level), "space_id": str(target_space or "")},
            )
            view = _view(log)
            session.commit()
            return view

    def grant(self, log_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Add an L1 named grant. Idempotent."""
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            log = _load(session, log_id)
            _require_author(actor, log)

            target = session.get(User, user_id)
            if target is None or target.status is UserStatus.DEACTIVATED:
                raise NotFound(GRANTEE_NOT_FOUND)

            existing = session.scalar(
                select(LogShareGrant.id).where(
                    LogShareGrant.log_id == log.id, LogShareGrant.user_id == user_id
                )
            )
            if existing is not None:
                return

            session.add(
                LogShareGrant(
                    tenant_id=log.tenant_id,
                    log_id=log.id,
                    user_id=user_id,
                    granted_by=actor.user_id,
                )
            )
            audit.record(
                session,
                "log.share_granted",
                target_type="log",
                target_id=log.id,
                after={"user": str(user_id)},
            )
            session.commit()

    def revoke(self, log_id: uuid.UUID, user_id: uuid.UUID) -> None:
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            log = _load(session, log_id)
            _require_author(actor, log)

            row = session.scalars(
                select(LogShareGrant).where(
                    LogShareGrant.log_id == log.id, LogShareGrant.user_id == user_id
                )
            ).first()
            if row is None:
                return
            session.delete(row)
            audit.record(
                session,
                "log.share_revoked",
                target_type="log",
                target_id=log.id,
                before={"user": str(user_id)},
            )
            session.commit()

    def grantees(self, log_id: uuid.UUID) -> list[uuid.UUID]:
        with tenant_session() as session:
            actor = actor_principal(session)
            log = _load(session, log_id)
            audited = _require_readable(session, actor, log, via="grantees")
            users = list(
                session.scalars(
                    select(LogShareGrant.user_id).where(LogShareGrant.log_id == log.id)
                )
            )
            if audited:
                session.commit()
            return users

    # ------------------------------------------------------------- LOG-9

    def mark_knowledge_candidate(
        self, log_id: uuid.UUID, marked: bool = True, *, now: dt.datetime | None = None
    ) -> LogView:
        """LOG-9 🔒 — the field and the checkbox, nothing else in S1.

        Author or Admin, matching the edit rule. Opening it to any reader is a
        Phase-2 question, to answer when RAG actually consumes the signal;
        ``marked_by`` is singular, so a second marker would overwrite the first
        and the provenance would be worse, not better.

        Worth more the longer BOT and RAG slip: every log written from day one
        carries a human judgment about whether it belongs in the knowledge base,
        so RAG can backfill history instead of running a re-annotation pass.
        """
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.LOG_WRITE)
            log = _load(session, log_id)
            _require_author(actor, log)

            log.knowledge_candidate = marked
            log.marked_by = actor.user_id if marked else None
            log.marked_at = now if marked else None
            audit.record(
                session,
                "log.knowledge_marked" if marked else "log.knowledge_unmarked",
                target_type="log",
                target_id=log.id,
                after={"knowledge_candidate": marked},
            )
            view = _view(log)
            session.commit()
            return view

    def knowledge_candidate_count(self) -> int:
        """S-16's counting rule: **checked and body ≥ 300 characters.**

        Both halves matter. The checkbox alone would count a one-line note
        somebody ticked out of optimism; the length alone would count every long
        log nobody judged. Ten are spot-checked by hand before acceptance —
        agreed now rather than argued about during the review.
        """
        with tenant_session() as session:
            require(actor_principal(session), Capability.CONTENT_VIEW)
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(Log)
                    .where(
                        Log.knowledge_candidate.is_(True),
                        func.char_length(Log.body) >= KNOWLEDGE_MIN_BODY,
                    )
                )
                or 0
            )

    # ----------------------------------------------------------------- reads

    def get(self, log_id: uuid.UUID) -> LogView:
        with tenant_session() as session:
            actor = actor_principal(session)
            log = _load(session, log_id)
            audited = _require_readable(session, actor, log, via="get")
            view = _view(log)
            if audited:
                # S-19. The commit is here rather than unconditional so that
                # reading a log stays a read for everyone whose read is ordinary.
                session.commit()
            return view

    def list(self, limit: int = 50) -> list[LogView]:
        """Logs this reader may see, newest-updated first.

        ``visible_logs_predicate`` is the SQL mirror of
        :func:`relay.app.logs.sharing.can_read`. Two implementations of one rule
        is a real risk, so ``test_logs.py`` cross-checks every log against both.
        Filtering in Python instead would be one implementation — and would also
        read every log in the tenant to return five.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.CONTENT_VIEW)
            rows = session.scalars(
                select(Log)
                .where(visible_logs_predicate(actor.user_id, actor.role))
                .order_by(Log.updated_at.desc(), Log.id.desc())
                .limit(limit)
            ).all()
            views = [_view(row) for row in rows]
            # One row for the whole list, naming the logs an ordinary Member
            # would not have seen (S-19) — a list *is* one act, and a row per
            # entry would bury the ones worth reading.
            if read_audit.record_many(session, actor, rows, via="list"):
                session.commit()
            return views


# ---------------------------------------------------------------- internals


def _load(session, log_id: uuid.UUID) -> Log:
    log = session.get(Log, log_id)
    if log is None:
        raise NotFound(LOG_NOT_FOUND)
    return log


def _version(session, log_id: uuid.UUID, version_no: int) -> LogVersion:
    row = session.scalars(
        select(LogVersion).where(
            LogVersion.log_id == log_id, LogVersion.version_no == version_no
        )
    ).first()
    if row is None:
        raise NotFound(VERSION_NOT_FOUND)
    return row


def _require_author(actor: Principal, log: Log) -> None:
    """§5.4: a Member edits their **own** logs. Admin is the §6.3 exception."""
    if actor.user_id == log.author_id or actor.role is Role.ADMIN:
        return
    raise PermissionDenied(NOT_THE_AUTHOR)


def _require_readable(session, actor: Principal, log: Log, *, via: str = "get") -> bool:
    """Refuse with ``NotFound``, not ``PermissionDenied``.

    A log the reader may not see is a log they should not learn exists — the
    same reasoning as MT-6's 404-not-403, applied within a tenant. LOG-3's
    inline ticket cards make the same choice for the same reason: degrade to
    plain text, never leak the title.

    Returns **whether an audit row was written** (S-19): the two membership facts
    the share-level rule needs are the same two the privilege counterfactual
    needs, so this is the one place that has them both. The caller commits if it
    is True — that is the only reason a read path ever commits.
    """
    has_named_grant = _has_grant(session, log, actor)
    is_space_member = _in_space(session, log, actor)
    if not can_read(
        share_level=log.share_level,
        author_id=log.author_id,
        reader=Reader(user_id=actor.user_id, role=actor.role),
        has_named_grant=has_named_grant,
        is_space_member=is_space_member,
    ):
        raise NotFound(LOG_NOT_FOUND)
    return read_audit.record_one(
        session,
        actor,
        log,
        via=via,
        has_named_grant=has_named_grant,
        is_space_member=is_space_member,
    )


def _has_grant(session, log: Log, actor: Principal) -> bool:
    if log.share_level is not ShareLevel.NAMED:
        return False
    return (
        session.scalar(
            select(LogShareGrant.id).where(
                LogShareGrant.log_id == log.id, LogShareGrant.user_id == actor.user_id
            )
        )
        is not None
    )


def _in_space(session, log: Log, actor: Principal) -> bool:
    if log.share_level is not ShareLevel.SPACE or log.space_id is None:
        return False
    return (
        session.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == log.space_id, SpaceMember.user_id == actor.user_id
            )
        )
        is not None
    )


def _check_space(share_level: ShareLevel, space_id: uuid.UUID | None) -> None:
    """L2 without a space would be a level that grants nothing, silently."""
    if share_level is ShareLevel.SPACE and space_id is None:
        raise ValidationFailed(SPACE_REQUIRED)


def _view(log: Log) -> LogView:
    return LogView(
        id=log.id,
        title=log.title,
        body=log.body,
        format=log.format,
        share_level=log.share_level,
        space_id=log.space_id,
        author_id=log.author_id,
        current_version=log.current_version,
        knowledge_candidate=log.knowledge_candidate,
        marked_by=log.marked_by,
        marked_at=log.marked_at,
        updated_at=log.updated_at,
    )
