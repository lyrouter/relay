"""LOG-5 · uploads, and the two-step that S-11 requires.

**The link is not the credential.** Every download is permission-checked first
(``AttachmentService.link``) and only then handed a 5-minute signed URL. The
signature exists so that a link, once handed out, stops working — it is not
"the URL is unguessable", which is a hope rather than access control.

That is why there are two routes and not one. ``/link`` is authenticated and
authorized; ``/blobs/{key}`` is neither, because by the time a browser follows the
URL the decision has already been made and recorded. What protects that second
hop is the HMAC and the expiry.

``/blobs/{key}`` is also the one route that disappears: it exists because the
current carrier is the filesystem store. With MinIO (F-4) the signed URL points
at the object store and the browser never comes back here. Same keys either way,
so nothing moves when the carrier changes (owner action O-5).
"""

from __future__ import annotations

import mimetypes
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from relay.api.dependencies import Session
from relay.api.wiring import blob_store
from relay.app.logs.attachments import AttachmentService
from relay.infra.blob.filesystem import InvalidSignature

router = APIRouter(tags=["attachments"])

LINK_INVALID = "链接无效或已过期，请重新打开页面。"

#: Streamed in chunks so a 25 MiB attachment never has to fit in memory twice.
CHUNK = 64 * 1024


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    owner_type: str
    owner_id: uuid.UUID
    filename: str
    size: int
    mime: str
    #: The virus-scan hook's verdict. ``skipped`` is the honest answer when no
    #: scanner is wired up — never ``clean``, which would be a lie the UI would
    #: repeat.
    scan_state: str
    uploaded_by: uuid.UUID


class LinkResponse(BaseModel):
    #: Relative, and short-lived. The public origin is deployment configuration,
    #: and baking it into a stored value is how links outlive the hostname they
    #: were minted for.
    url: str


def _view(one) -> AttachmentResponse:
    return AttachmentResponse(
        id=one.id,
        owner_type=one.owner_type,
        owner_id=one.owner_id,
        filename=one.filename,
        size=one.size,
        mime=one.mime,
        scan_state=one.scan_state,
        uploaded_by=one.uploaded_by,
    )


@router.post(
    "/web/attachments", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED
)
def upload(
    session: Session,
    owner_type: Annotated[str, Form()],
    owner_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> AttachmentResponse:
    """Attach a file to a log or a ticket.

    The permission required is the one for *writing the owner* — attaching to a
    log is editing it — and the size and MIME limits are the service's, so this
    route cannot accidentally be the lenient path.
    """
    view = AttachmentService(blob_store()).upload(
        owner_type,
        owner_id,
        file.filename or "file",
        file.content_type or "application/octet-stream",
        file.file,
    )
    return _view(view)


@router.get("/web/attachments", response_model=list[AttachmentResponse])
def list_attachments(
    session: Session,
    owner_type: Annotated[str, Query()],
    owner_id: Annotated[uuid.UUID, Query()],
) -> list[AttachmentResponse]:
    service = AttachmentService(blob_store())
    return [_view(one) for one in service.list_for(owner_type, owner_id)]


@router.get("/web/attachments/{attachment_id}/link", response_model=LinkResponse)
def link(attachment_id: uuid.UUID, session: Session) -> LinkResponse:
    """Permission-check, then mint. Returns JSON rather than a redirect so the
    caller can put the URL in an ``<img src>`` — a redirect would work for a
    download and not for a rendered image inside a Markdown preview."""
    return LinkResponse(url=AttachmentService(blob_store()).link(attachment_id))


@router.delete("/web/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(attachment_id: uuid.UUID, session: Session) -> Response:
    AttachmentService(blob_store()).delete(attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/blobs/{key:path}", include_in_schema=False)
def serve_blob(key: str, expires: int, sig: str) -> StreamingResponse:
    """Serve a signed object. **No session, by design** — see the module note.

    Excluded from the OpenAPI schema because it is not part of any contract: it
    is the current carrier's delivery detail, and the frontend never constructs
    one of these URLs itself. It follows the one ``/link`` handed it.
    """
    store = blob_store()
    try:
        store.verify(key, expires, sig)
        stream = store.open(key)
    except (InvalidSignature, FileNotFoundError) as exc:
        # One answer for forged, expired and missing. Distinguishing them would
        # turn this route into an oracle for which keys exist.
        raise HTTPException(status_code=404, detail=LINK_INVALID) from exc

    filename = key.rsplit("/", 1)[-1]
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return StreamingResponse(
        iter(lambda: stream.read(CHUNK), b""),
        media_type=mime,
        headers={
            # ``inline`` so an image renders in the editor preview; the filename
            # was already sanitised on the way in (``safe_filename``), which is
            # what keeps it safe to put in a header.
            "Content-Disposition": f'inline; filename="{filename}"',
            # A signed link is per-viewer and short-lived; a shared cache holding
            # it would serve it to somebody the check never ran for.
            "Cache-Control": "private, max-age=60",
        },
    )
