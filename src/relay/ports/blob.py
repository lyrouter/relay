"""BlobPort (LOG-5) — the one thing RLS does not cover.

Isolation here is not a policy, it is two mechanics that have to be right:

* the object key **contains ``tenant_id``** by construction, so a key from one
  tenant cannot name an object in another;
* access is **permission-checked first, then served by a 5-minute signed link**
  (decided, S-11). Never "the URL is unguessable" — that is not access control,
  it is a hope.

The store is self-hosted MinIO (F-4), which means attachments are inside the
backup scope. INT-11's restore drill must cover PostgreSQL and MinIO **together**:
restoring only PG yields intact prose with every image broken, and a half-restore
that never shows up in a drill shows up during a real incident instead.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol


class BlobTooLarge(ValueError):
    """Raised **while streaming**, so the limit costs one chunk rather than a file.

    Declared on the port rather than on a carrier because the timing is part of
    the contract, not an implementation detail: a carrier that checked the size
    after the transfer would still enforce the 25 MiB cap and would no longer
    protect bandwidth or disk. S-25 calls this out as one of the two semantics
    the MinIO adapter has to align explicitly.
    """


@dataclass(frozen=True, slots=True)
class BlobRef:
    key: str
    size: int
    mime: str


def tenant_prefix(tenant_id: uuid.UUID) -> str:
    """Every key starts here. Kept as a function so there is exactly one
    definition of the layout to audit."""
    return f"t/{tenant_id}/"


def safe_filename(filename: str) -> str:
    """Strip a filename down to something safe to store and to serve.

    Applied to the **stored** name as well as to the key. The name is display
    metadata, but it is display metadata that ends up in a
    ``Content-Disposition`` header and in whatever the browser writes to disk —
    so ``../../etc/passwd`` must not survive into the database either. Nothing
    is lost: the key's random segment is what makes an object unique, so the
    path structure of the original name carries no information.

    Part of the port rather than of one carrier: every implementation has to
    apply the same rule, or the same upload gets two different names.
    """
    cleaned = "".join(
        char
        for char in PurePosixPath(filename or "file").name
        if char.isalnum() or char in "._- "
    ).strip()
    return cleaned or "file"


class BlobPort(Protocol):
    def put(
        self, tenant_id: uuid.UUID, filename: str, mime: str, stream: BinaryIO
    ) -> BlobRef:
        """Raises :class:`BlobTooLarge` as soon as a chunk crosses the limit."""
        ...

    def signed_url(self, key: str, ttl: dt.timedelta = dt.timedelta(minutes=5)) -> str:
        ...

    def delete(self, key: str) -> None:
        ...
