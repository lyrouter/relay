"""LOG-5 · a filesystem BlobPort, and the key layout every carrier shares.

The S1 carrier is **self-hosted MinIO** (F-4). This implementation is not a
stand-in for it in production — it is the development and test carrier, and it
exists in the same file layout so that swapping in an S3 client moves no object
and changes no stored key. ``attachment.blob_key`` is what the database holds,
and it must mean the same thing under both.

Two mechanics carry the isolation the object store cannot get from RLS (S-11):

* **the key contains ``tenant_id``** by construction (``relay.ports.blob``
  owns the one definition of the prefix), so a key minted for one tenant cannot
  name an object in another;
* **access is permission-checked first, then served by a short-lived signed
  link.** The signature here is an HMAC over key and expiry, so a leaked link
  stops working — as opposed to "the URL is unguessable", which is not access
  control but a hope.

The signature is not a substitute for the permission check that precedes it. It
is what stops a link, once handed out, from becoming a permanent capability.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets
import uuid
from pathlib import Path
from typing import BinaryIO

from relay.config import settings
from relay.ports.blob import BlobRef, BlobTooLarge, safe_filename, tenant_prefix

#: Read in chunks so a large upload never has to fit in memory at once.
CHUNK = 64 * 1024

#: Re-exported: ``BlobTooLarge`` moved to the port when the second carrier
#: arrived (S-25), because the *timing* of the check is a contract both owe.
__all__ = ["BlobTooLarge", "FilesystemBlobStore", "InvalidSignature"]


class InvalidSignature(ValueError):
    pass


class FilesystemBlobStore:
    """Implements :class:`relay.ports.blob.BlobPort`."""

    def __init__(self, root: str | None = None, max_bytes: int | None = None) -> None:
        self._root = Path(root or settings.blob_root).resolve()
        self._max_bytes = max_bytes or settings.blob_max_bytes

    def put(
        self, tenant_id: uuid.UUID, filename: str, mime: str, stream: BinaryIO
    ) -> BlobRef:
        """Store an object under ``t/<tenant_id>/<random>/<filename>``.

        The random segment is there so that two uploads of ``screenshot.png``
        into one tenant do not collide, and so that a key cannot be guessed from
        a filename somebody mentioned in a ticket.
        """
        key = f"{tenant_prefix(tenant_id)}{secrets.token_urlsafe(16)}/{safe_filename(filename)}"
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        size = 0
        try:
            with path.open("wb") as out:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise BlobTooLarge(
                            f"attachment exceeds {self._max_bytes} bytes"
                        )
                    out.write(chunk)
        except BaseException:
            # A half-written object is worse than none: it would pass a size
            # check on read and hand somebody a truncated file.
            path.unlink(missing_ok=True)
            raise

        return BlobRef(key=key, size=size, mime=mime)

    def signed_url(
        self, key: str, ttl: dt.timedelta = dt.timedelta(minutes=5)
    ) -> str:
        """A relative URL the API layer serves, valid for ``ttl``.

        Relative rather than absolute because the public origin is deployment
        configuration, and baking it into a stored value is how links outlive the
        hostname they were minted for.
        """
        expires = int((dt.datetime.now(dt.UTC) + ttl).timestamp())
        return f"/blobs/{key}?expires={expires}&sig={self._sign(key, expires)}"

    def verify(
        self,
        key: str,
        expires: int,
        signature: str,
        *,
        now: dt.datetime | None = None,
    ) -> None:
        """Raise unless the signature is ours and still valid.

        Order matters: the signature is checked before the clock, so an expired
        link and a forged one are indistinguishable to whoever presents them.
        """
        now = now or dt.datetime.now(dt.UTC)
        if not hmac.compare_digest(self._sign(key, expires), signature):
            raise InvalidSignature("链接无效或已过期。")
        if expires < int(now.timestamp()):
            raise InvalidSignature("链接无效或已过期。")

    def open(self, key: str) -> BinaryIO:
        path = self._path_for(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return path.open("rb")

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    # ------------------------------------------------------------- internals

    def _sign(self, key: str, expires: int) -> str:
        return hmac.new(
            settings.blob_signing_key.encode("utf-8"),
            f"{key}:{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _path_for(self, key: str) -> Path:
        """Resolve a key to a path, refusing anything that escapes the root.

        ``blob_key`` comes from our own ``put``, but it round-trips through the
        database and a URL before it gets back here — which is exactly the shape
        of a path-traversal bug, so the check is on the resolved path rather than
        on the string.
        """
        candidate = (self._root / key).resolve()
        if not str(candidate).startswith(str(self._root) + os.sep):
            raise ValueError(f"blob key escapes the store root: {key!r}")
        return candidate
