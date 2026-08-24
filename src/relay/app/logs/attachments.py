"""LOG-5 · attachments and images (design §6.4, decided S-11).

The object store is **the one thing RLS does not cover**, so isolation here is
not a policy — it is three mechanics that all have to be right, and each one is
load-bearing on its own:

1. **The key contains ``tenant_id``** by construction (``relay.ports.blob``
   defines the prefix once). A key minted for one tenant cannot name an object
   in another.
2. **Access is permission-checked, then signed.** :meth:`AttachmentService.link`
   resolves the owning log or ticket and applies LOG-6's rule *before* it asks
   the store for a URL. The signature is what stops the link outliving the
   check; it is not the check.
3. **The row is written in the same transaction as nothing else.** An attachment
   whose row committed but whose object did not is a broken image; an object
   with no row is an orphan nobody can find to delete. The object is written
   first and removed if the row fails, because an orphan is recoverable and a
   broken reference is not.

The virus-scan hook is a no-op in S1 and is called anyway. A hook that is added
"later" is a hook that is added at the same time as the scanner, which is the
same time as the first infected file — so the call site exists now and
``scan_state`` records that nothing looked.

**Attachments are outside PostgreSQL, which puts them inside INT-11's scope.**
Restoring only the database yields intact prose with every image broken, and a
half-restore that never appears in a drill appears during an incident instead.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from sqlalchemy import select

from relay.app import audit
from relay.app.authz import Principal, actor_principal, require
from relay.app.errors import NotFound, PayloadTooLarge, ValidationFailed
from relay.app.logs import read_audit
from relay.app.logs.sharing import Reader, can_read
from relay.config import settings
from relay.domain.enums import ShareLevel
from relay.domain.permissions import Capability
from relay.infra.db.models import Attachment, Log, LogShareGrant, SpaceMember, Ticket
from relay.infra.db.session import tenant_session
from relay.ports.blob import BlobTooLarge, safe_filename

OWNER_TYPES = ("log", "ticket")

ATTACHMENT_NOT_FOUND = "找不到该附件。"
OWNER_NOT_FOUND = "找不到附件要挂载的对象。"
UNSUPPORTED_TYPE = "不支持该文件类型。"
TOO_LARGE = "文件超过大小限制。"

#: An allowlist, not a blocklist. A blocklist of dangerous types is a list
#: somebody has to keep complete forever; this is what the product actually
#: needs to render or hand back.
ALLOWED_MIME_PREFIXES = (
    "image/",
    "text/plain",
    "application/pdf",
    "application/json",
    "application/zip",
    "application/gzip",
    "application/x-tar",
)


class BlobStore(Protocol):
    """The subset of ``BlobPort`` this service uses, plus ``delete``."""

    def put(
        self, tenant_id: uuid.UUID, filename: str, mime: str, stream: BinaryIO
    ) -> object: ...

    def signed_url(self, key: str, ttl: dt.timedelta = ...) -> str: ...

    def delete(self, key: str) -> None: ...


class Scanner(Protocol):
    """LOG-5's virus-scan hook. ``None`` means "not scanned", not "clean"."""

    def scan(self, key: str) -> str: ...


class NoopScanner:
    """The S1 implementation. Records that nothing looked, which is the honest
    value — reporting ``clean`` would be a lie that survives into an incident
    review."""

    def scan(self, key: str) -> str:
        return "skipped"


@dataclass(frozen=True, slots=True)
class AttachmentView:
    id: uuid.UUID
    owner_type: str
    owner_id: uuid.UUID
    filename: str
    size: int
    mime: str
    scan_state: str
    uploaded_by: uuid.UUID


class AttachmentService:
    """Runs inside an established ``TenantContext``."""

    def __init__(self, store: BlobStore, scanner: Scanner | None = None) -> None:
        self._store = store
        self._scanner = scanner or NoopScanner()

    def upload(
        self,
        owner_type: str,
        owner_id: uuid.UUID,
        filename: str,
        mime: str,
        stream: BinaryIO,
    ) -> AttachmentView:
        """Attach a file to a log or a ticket.

        The permission required is the one for *writing the owner*: attaching a
        file to a log is editing that log, so the same authorship rule applies,
        and attaching to a ticket needs ``TICKET_WRITE``.
        """
        if owner_type not in OWNER_TYPES:
            raise ValidationFailed(f"附件只能挂在 {', '.join(OWNER_TYPES)} 上。")
        _check_mime(mime)
        # Sanitised before it is stored, not only before it becomes a key: the
        # stored name ends up in a Content-Disposition header and in whatever
        # the browser writes to disk.
        display_name = safe_filename(filename)

        with tenant_session() as session:
            actor = actor_principal(session)
            owner = _load_owner(session, owner_type, owner_id)
            _require_owner_write(actor, owner_type, owner)

            # The object first: an orphan can be found and removed, a row
            # pointing at nothing shows up as a broken image in somebody's log.
            try:
                ref = self._store.put(actor.tenant_id, display_name, mime, stream)
            except BlobTooLarge as exc:
                # Translated here rather than at the route, so both carriers and
                # both HTTP surfaces answer 413 instead of leaking a ValueError
                # into the catch-all 500 handler.
                raise PayloadTooLarge(TOO_LARGE) from exc

            try:
                attachment = Attachment(
                    tenant_id=actor.tenant_id,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    blob_key=ref.key,
                    filename=display_name,
                    size=ref.size,
                    mime=mime,
                    uploaded_by=actor.user_id,
                    scan_state=self._scanner.scan(ref.key),
                )
                session.add(attachment)
                session.flush()
                audit.record(
                    session,
                    "attachment.uploaded",
                    target_type=owner_type,
                    target_id=owner_id,
                    after={"filename": display_name, "size": ref.size, "mime": mime},
                )
                view = _view(attachment)
                session.commit()
                return view
            except BaseException:
                self._store.delete(ref.key)
                raise

    def link(self, attachment_id: uuid.UUID) -> str:
        """Permission-check, then hand back a short-lived signed URL (S-11).

        The two steps are in this order and neither is optional. Skipping the
        check and relying on the signature would make the link itself the
        credential — which is the "URL is unguessable" model the decision
        explicitly rejects.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            attachment = _load(session, attachment_id)
            owner = _load_owner(session, attachment.owner_type, attachment.owner_id)
            audited = _require_owner_read(session, actor, attachment.owner_type, owner)
            url = self._store.signed_url(
                attachment.blob_key,
                ttl=dt.timedelta(seconds=settings.blob_link_ttl_seconds),
            )
            if audited:
                session.commit()
            return url

    def list_for(self, owner_type: str, owner_id: uuid.UUID) -> list[AttachmentView]:
        with tenant_session() as session:
            actor = actor_principal(session)
            owner = _load_owner(session, owner_type, owner_id)
            audited = _require_owner_read(session, actor, owner_type, owner)
            rows = session.scalars(
                select(Attachment)
                .where(Attachment.owner_type == owner_type, Attachment.owner_id == owner_id)
                .order_by(Attachment.created_at.asc())
            ).all()
            views = [_view(row) for row in rows]
            if audited:
                session.commit()
            return views

    def delete(self, attachment_id: uuid.UUID) -> None:
        """Remove the row, then the object.

        This order is the opposite of upload's, and for the same reason: if the
        object delete fails, what is left is an orphan rather than a row nobody
        can render.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            attachment = _load(session, attachment_id)
            owner = _load_owner(session, attachment.owner_type, attachment.owner_id)
            _require_owner_write(actor, attachment.owner_type, owner)

            key = attachment.blob_key
            session.delete(attachment)
            audit.record(
                session,
                "attachment.deleted",
                target_type=attachment.owner_type,
                target_id=attachment.owner_id,
                before={"filename": attachment.filename},
            )
            session.commit()

        self._store.delete(key)


# ---------------------------------------------------------------- internals


def _check_mime(mime: str) -> None:
    if not any((mime or "").startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        raise ValidationFailed(UNSUPPORTED_TYPE)


def _load(session, attachment_id: uuid.UUID) -> Attachment:
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise NotFound(ATTACHMENT_NOT_FOUND)
    return attachment


def _load_owner(session, owner_type: str, owner_id: uuid.UUID):
    model = {"log": Log, "ticket": Ticket}[owner_type]
    owner = session.get(model, owner_id)
    if owner is None:
        raise NotFound(OWNER_NOT_FOUND)
    return owner


def _require_owner_write(actor: Principal, owner_type: str, owner) -> None:
    from relay.app.logs.service import _require_author  # local: avoids a cycle

    if owner_type == "log":
        require(actor, Capability.LOG_WRITE)
        _require_author(actor, owner)
        return
    require(actor, Capability.TICKET_WRITE)


def _require_owner_read(session, actor: Principal, owner_type: str, owner) -> bool:
    """Refused as ``NotFound``: an attachment on a log you cannot read is one you
    should not learn exists, same as the log itself.

    Returns whether a S-19 audit row was written, which the caller commits.
    """
    if owner_type == "ticket":
        from relay.app.tickets.service import require_readable  # local: avoids a cycle

        # Tenant-wide for a Member or an Admin; a Guest reaches only their own
        # tickets (S-21). An attachment inherits its owner's readability — the
        # file cannot be more visible than the ticket it hangs on.
        require(actor, Capability.CONTENT_VIEW)
        require_readable(actor, owner)
        return False

    has_named_grant = _has_grant(session, owner, actor)
    is_space_member = _in_space(session, owner, actor)
    readable = can_read(
        share_level=owner.share_level,
        author_id=owner.author_id,
        reader=Reader(user_id=actor.user_id, role=actor.role),
        has_named_grant=has_named_grant,
        is_space_member=is_space_member,
    )
    if not readable:
        raise NotFound(ATTACHMENT_NOT_FOUND)
    # S-19: fetching the file off a colleague's private log is reading it.
    return read_audit.record_one(
        session,
        actor,
        owner,
        via="attachment",
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


def _view(attachment: Attachment) -> AttachmentView:
    return AttachmentView(
        id=attachment.id,
        owner_type=attachment.owner_type,
        owner_id=attachment.owner_id,
        filename=attachment.filename,
        size=attachment.size,
        mime=attachment.mime,
        scan_state=attachment.scan_state,
        uploaded_by=attachment.uploaded_by,
    )
