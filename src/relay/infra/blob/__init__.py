"""BlobPort implementations (LOG-5).

Two carriers, one key layout. ``FilesystemBlobStore`` is development and test,
``MinioBlobStore`` is the deployed one (F-4, written blind under S-25) — and
because ``relay.ports.blob`` owns the key layout, switching between them moves no
object and rewrites no stored ``blob_key``.

Which one runs is decided **once**, in ``relay.api.wiring``. See its note on why
that switch has to be visible rather than per-call-site.
"""

from relay.infra.blob.filesystem import FilesystemBlobStore
from relay.infra.blob.minio import MinioBlobStore

__all__ = ["FilesystemBlobStore", "MinioBlobStore"]
