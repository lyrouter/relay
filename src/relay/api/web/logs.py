"""LOG-1…LOG-9 · the surface the editor talks to.

Three route shapes here carry a decision rather than a mapping:

**PATCH distinguishes absent from null.** ``LogService.save`` takes ``UNSET`` for
"unchanged" and an explicit value for "set to this", and autosave depends on it:
the editor sends the body every few seconds and the title only when it changes.
Pydantic's ``model_fields_set`` is what preserves the difference, and without it
every autosave would blank the title.

**The edit lock is four routes, not a flag on save.** S-7 gives the lock a TTL, a
heartbeat and a takeover that reports the version the previous editor's work is
safe in, and all three are things the UI has to *show*. Folding them into save
would leave the frontend guessing why a write failed.

**Rollback is a POST, not a PUT of an old version.** §6.2: rollback appends a new
version holding old content, so it creates something. A PUT would suggest history
was rewritten, which is exactly what the design forbids.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from relay.api.dependencies import Session
from relay.app.errors import PayloadTooLarge
from relay.app.logs.locks import EditLockService
from relay.app.logs.service import UNSET, LogService
from relay.domain.diffs import LineOp
from relay.domain.enums import LogFormat, ShareLevel
from relay.domain.note_import import MAX_BYTES as IMPORT_MAX_BYTES

router = APIRouter(prefix="/web/logs", tags=["logs"])


class LogResponse(BaseModel):
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


class CreateLogPayload(BaseModel):
    title: str = Field(min_length=1)
    body: str = ""
    format: LogFormat = LogFormat.MARKDOWN
    #: L0 by default, matching the service: a draft that starts visible is a
    #: draft somebody reads mid-thought, and the cost of the wrong default runs
    #: one way only.
    share_level: ShareLevel = ShareLevel.PRIVATE
    space_id: uuid.UUID | None = None


class SaveLogPayload(BaseModel):
    """Absent means unchanged. See the module note — this is what autosave needs."""

    title: str | None = None
    body: str | None = None


class VersionResponse(BaseModel):
    version_no: int
    title: str
    author_id: uuid.UUID
    created_at: dt.datetime | None
    #: Set when this version was produced by a rollback, naming its source (§6.2).
    rolled_back_from: int | None


class DiffLineResponse(BaseModel):
    op: LineOp
    text: str
    old_no: int | None
    new_no: int | None


class RollbackPayload(BaseModel):
    to_version: int = Field(ge=1)


class SharePayload(BaseModel):
    share_level: ShareLevel
    #: Only meaningful for L2. Absent leaves the log's current space alone, which
    #: is why it is optional rather than defaulting to None — sending None means
    #: "take it out of its space".
    space_id: uuid.UUID | None = None


class GrantPayload(BaseModel):
    user_id: uuid.UUID


class KnowledgePayload(BaseModel):
    marked: bool = True


class LockResponse(BaseModel):
    log_id: uuid.UUID
    holder_id: uuid.UUID
    expires_at: dt.datetime
    #: Set when this acquire took a lapsed lock from somebody else…
    taken_over_from: uuid.UUID | None
    #: …and the version their work is safe in. S-7 asks the UI to show this
    #: sentence, which is only possible if the number reaches it.
    last_saved_version: int | None


class KnowledgeCountResponse(BaseModel):
    #: S-16: checked **and** body ≥ 300 characters. INT-8's dashboard reads this.
    count: int


def _log(view) -> LogResponse:
    return LogResponse(
        id=view.id,
        title=view.title,
        body=view.body,
        format=view.format,
        share_level=view.share_level,
        space_id=view.space_id,
        author_id=view.author_id,
        current_version=view.current_version,
        knowledge_candidate=view.knowledge_candidate,
        marked_by=view.marked_by,
        marked_at=view.marked_at,
        updated_at=view.updated_at,
    )


@router.get("", response_model=list[LogResponse])
def list_logs(session: Session, limit: int = 50) -> list[LogResponse]:
    """Only what this reader may see — the share-level filter runs in SQL, so a
    list of five does not read every log in the tenant (LOG-6)."""
    return [_log(one) for one in LogService().list(limit=limit)]


@router.post("", response_model=LogResponse, status_code=status.HTTP_201_CREATED)
def create_log(payload: CreateLogPayload, session: Session) -> LogResponse:
    return _log(
        LogService().create(
            payload.title,
            payload.body,
            format=payload.format,
            space_id=payload.space_id,
            share_level=payload.share_level,
        )
    )


@router.get("/knowledge-count", response_model=KnowledgeCountResponse)
def knowledge_count(session: Session) -> KnowledgeCountResponse:
    """LOG-9 / INT-8. Declared before ``/{log_id}`` or the path would swallow it."""
    return KnowledgeCountResponse(count=LogService().knowledge_candidate_count())


#: Streamed in chunks so a mis-selected video is refused before it fills RAM.
#: The cap is the domain's; this is only the read window.
_IMPORT_CHUNK = 64 * 1024


def _read_import(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(_IMPORT_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > IMPORT_MAX_BYTES:
            raise PayloadTooLarge(f"文件不能超过 {IMPORT_MAX_BYTES // (1024 * 1024)} MB。")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/import", response_model=LogResponse, status_code=status.HTTP_201_CREATED)
def import_log(file: Annotated[UploadFile, File()], session: Session) -> LogResponse:
    """Turn an uploaded Markdown or HTML file into a log.

    Declared before ``/{log_id}`` for the same reason as ``/knowledge-count``.
    The imported note is an ordinary log (Markdown, private, knowledge-marked),
    so 浏览 and 编辑 work without a second reader or editor.
    """
    return _log(LogService().import_note(file.filename or "未命名.md", _read_import(file)))


@router.get("/{log_id}", response_model=LogResponse)
def get_log(log_id: uuid.UUID, session: Session) -> LogResponse:
    return _log(LogService().get(log_id))


@router.patch("/{log_id}", response_model=LogResponse)
def save_log(log_id: uuid.UUID, payload: SaveLogPayload, session: Session) -> LogResponse:
    """Save or autosave. An unchanged save is a no-op, not an error: autosave
    fires on a timer and most of those timers fire on text nobody touched."""
    sent = payload.model_fields_set
    return _log(
        LogService().save(
            log_id,
            title=payload.title if "title" in sent else UNSET,
            body=payload.body if "body" in sent else UNSET,
        )
    )


@router.get("/{log_id}/versions", response_model=list[VersionResponse])
def log_versions(log_id: uuid.UUID, session: Session) -> list[VersionResponse]:
    return [
        VersionResponse(
            version_no=one.version_no,
            title=one.title,
            author_id=one.author_id,
            created_at=one.created_at,
            rolled_back_from=one.rolled_back_from,
        )
        for one in LogService().versions(log_id)
    ]


@router.get("/{log_id}/diff", response_model=list[DiffLineResponse])
def log_diff(
    log_id: uuid.UUID,
    session: Session,
    from_version: Annotated[int, Query(ge=1)],
    to_version: Annotated[int, Query(ge=1)],
) -> list[DiffLineResponse]:
    return [
        DiffLineResponse(op=line.op, text=line.text, old_no=line.old_no, new_no=line.new_no)
        for line in LogService().diff(log_id, from_version, to_version)
    ]


@router.post("/{log_id}/rollback", response_model=LogResponse)
def rollback_log(log_id: uuid.UUID, payload: RollbackPayload, session: Session) -> LogResponse:
    return _log(LogService().rollback(log_id, payload.to_version))


@router.put("/{log_id}/share", response_model=LogResponse)
def set_share_level(log_id: uuid.UUID, payload: SharePayload, session: Session) -> LogResponse:
    sent = payload.model_fields_set
    return _log(
        LogService().set_share_level(
            log_id,
            payload.share_level,
            space_id=payload.space_id if "space_id" in sent else UNSET,
        )
    )


@router.get("/{log_id}/grants", response_model=list[uuid.UUID])
def list_grants(log_id: uuid.UUID, session: Session) -> list[uuid.UUID]:
    return LogService().grantees(log_id)


@router.post("/{log_id}/grants", status_code=status.HTTP_204_NO_CONTENT)
def add_grant(log_id: uuid.UUID, payload: GrantPayload, session: Session) -> Response:
    """L1, by name. Idempotent — re-granting is not an error, because the UI
    cannot always know what the server already has."""
    LogService().grant(log_id, payload.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{log_id}/grants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_grant(log_id: uuid.UUID, user_id: uuid.UUID, session: Session) -> Response:
    LogService().revoke(log_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{log_id}/knowledge", response_model=LogResponse)
def mark_knowledge(
    log_id: uuid.UUID, payload: KnowledgePayload, session: Session
) -> LogResponse:
    """LOG-9 · the checkbox. Author or Admin, matching the edit rule."""
    return _log(LogService().mark_knowledge_candidate(log_id, payload.marked))


# ----------------------------------------------------------------- edit lock


def _lock(view) -> LockResponse:
    return LockResponse(
        log_id=view.log_id,
        holder_id=view.holder_id,
        expires_at=view.expires_at,
        taken_over_from=view.taken_over_from,
        last_saved_version=view.last_saved_version,
    )


@router.post("/{log_id}/lock", response_model=LockResponse)
def acquire_lock(log_id: uuid.UUID, session: Session) -> LockResponse:
    """Take the lock, or be told who holds it and for how long (S-7).

    A refusal is a 409 carrying that sentence, which is why the failure is worth
    more than a boolean: "someone else is editing" without a name and a countdown
    leaves the user with nothing to do.
    """
    return _lock(EditLockService().acquire(log_id))


@router.post("/{log_id}/lock/heartbeat", response_model=LockResponse)
def heartbeat_lock(log_id: uuid.UUID, session: Session) -> LockResponse:
    return _lock(EditLockService().heartbeat(log_id))


@router.get("/{log_id}/lock", response_model=LockResponse | None)
def lock_holder(log_id: uuid.UUID, session: Session) -> LockResponse | None:
    holder = EditLockService().holder(log_id)
    return _lock(holder) if holder else None


@router.delete("/{log_id}/lock", status_code=status.HTTP_204_NO_CONTENT)
def release_lock(log_id: uuid.UUID, session: Session) -> Response:
    EditLockService().release(log_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
