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
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class BlobRef:
    key: str
    size: int
    mime: str


def tenant_prefix(tenant_id: uuid.UUID) -> str:
    """Every key starts here. Kept as a function so there is exactly one
    definition of the layout to audit."""
    return f"t/{tenant_id}/"


class BlobPort(Protocol):
    def put(
        self, tenant_id: uuid.UUID, filename: str, mime: str, stream: BinaryIO
    ) -> BlobRef:
        ...

    def signed_url(self, key: str, ttl: dt.timedelta = dt.timedelta(minutes=5)) -> str:
        ...

    def delete(self, key: str) -> None:
        ...
