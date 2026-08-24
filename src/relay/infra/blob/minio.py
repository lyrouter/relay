"""LOG-5 · the S3 carrier, written blind against standard S3 semantics (S-25).

**Why this file exists before the instance does.** O-5 asked for MinIO's address,
credentials and bucket name before the adapter could be written; S-25 decided the
opposite way — write it against **standard S3 semantics** and treat what a real
instance disagrees about as a BUG under LOG-5. So the four things operations was
going to hand over became deployment configuration (``relay.config``), and this
adapter assumes only what the protocol guarantees.

**What blind writing can and cannot get right**, stated plainly because it
decides where the first bug will be:

* it *can* get the interface and the semantics right — the key layout, the
  5-minute presigned link, the streaming size limit, the error mapping. Those are
  protocol-level and are exercised by ``tests/test_blob_contract.py`` against a
  real ``minio/minio`` container;
* it *cannot* get the deployment shape right — which endpoint a browser can
  reach, who created the bucket, whether the clocks agree. Those live in
  ``markdown/relay-s1-deploy.md`` and in ``scripts/check_blob_store.py``, which
  exists so a real instance proves itself on day one rather than during the
  launch window.

Two semantics are aligned **here, explicitly**, rather than left for a runtime to
reveal:

1. **The size limit stays a streaming decision.** ``BlobTooLarge`` is raised
   after the chunk that crosses the limit, not after the upload finishes, so a
   25 MiB cap still protects bandwidth and disk. A naive S3 port (hand the SDK a
   file object and check the length afterwards) keeps the limit and loses the
   protection — the file arrives in full and *then* gets refused.
2. **``verify`` and ``open`` do not exist on this class.** They are the
   filesystem carrier's delivery mechanics, and with S3 the browser fetches the
   object directly. That absence is deliberate and load-bearing: ``/blobs/{key}``
   must go away when this carrier is selected, and a method that existed only to
   keep that route importable would hide the fact that it can no longer work.
   The composition root (``relay.api.wiring``) is where the switch is visible.

The key layout is ``relay.ports.blob``'s, unchanged: ``t/<tenant_id>/<random>/
<name>``. That is what makes the carrier swap free — **no object moves and no
stored ``blob_key`` is rewritten**, because both carriers spell a key the same
way.
"""

from __future__ import annotations

import datetime as dt
import functools
import secrets
import uuid
from typing import BinaryIO

from relay.config import settings
from relay.ports.blob import BlobRef, BlobTooLarge, safe_filename, tenant_prefix

#: Read from the client in the same 64 KiB steps the filesystem carrier uses, so
#: that "the limit costs one chunk" means the same thing under both carriers.
CHUNK = 64 * 1024

#: S3's minimum part size for every part but the last. Parts are buffered to this
#: size before being sent; below it, the object goes up in one request.
PART_SIZE = 8 * 1024 * 1024


class BlobStoreUnavailable(RuntimeError):
    """The carrier could not be reached or is misconfigured.

    Separate from :class:`FileNotFoundError` because the operational answers are
    opposites: a missing object is somebody's stale link, an unavailable store is
    every attachment in the product being broken at once.
    """


class MinioBlobStore:
    """Implements :class:`relay.ports.blob.BlobPort` against any S3-compatible
    endpoint; configured for MinIO (F-4).

    Two clients, not one, and the reason is blind spot #1 from the S-25
    write-up: **a presigned URL embeds the endpoint of the client that signed
    it.** MinIO is typically deployed as an internal endpoint plus a
    reverse-proxied public name, so signing with the connection endpoint yields
    links the browser cannot resolve — and the failure is silent here, because
    the browser goes straight to the object store and never comes back to the
    application. So the signing client is built against
    ``RELAY_MINIO_PUBLIC_ENDPOINT`` when one is set.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        public_endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        region: str | None = None,
        path_style: bool | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self._endpoint = endpoint or settings.minio_endpoint
        self._public_endpoint = (
            public_endpoint
            if public_endpoint is not None
            else (settings.minio_public_endpoint or self._endpoint)
        )
        self._access_key = access_key or settings.minio_access_key
        self._secret_key = secret_key or settings.minio_secret_key
        self._bucket = bucket or settings.minio_bucket
        self._region = region or settings.minio_region
        self._path_style = settings.minio_path_style if path_style is None else path_style
        self._max_bytes = max_bytes or settings.blob_max_bytes
        if not self._endpoint:
            raise BlobStoreUnavailable(
                "RELAY_MINIO_ENDPOINT is empty while the blob carrier is 'minio'. "
                "See markdown/relay-s1-deploy.md §O-5."
            )

    # ------------------------------------------------------------------ port

    def put(
        self, tenant_id: uuid.UUID, filename: str, mime: str, stream: BinaryIO
    ) -> BlobRef:
        """Upload under ``t/<tenant_id>/<random>/<filename>``, streaming.

        The random segment is the filesystem carrier's, for the same two reasons:
        two uploads of ``screenshot.png`` into one tenant must not collide, and a
        key must not be guessable from a filename somebody mentioned in a ticket.

        Multipart only when the object needs it. A single ``put_object`` for the
        common case avoids three round trips for a 40 KiB screenshot, and — more
        importantly — avoids leaving an aborted multipart upload behind for one.
        """
        key = f"{tenant_prefix(tenant_id)}{secrets.token_urlsafe(16)}/{safe_filename(filename)}"
        first = self._read_up_to(stream, PART_SIZE, already=0)

        if len(first) < PART_SIZE:
            # Fits in one request: the whole object is already in hand and was
            # size-checked while being read.
            self._call(
                "put_object", Bucket=self._bucket, Key=key, Body=first, ContentType=mime
            )
            return BlobRef(key=key, size=len(first), mime=mime)

        return self._put_multipart(key, mime, first, stream)

    def signed_url(self, key: str, ttl: dt.timedelta = dt.timedelta(minutes=5)) -> str:
        """A presigned GET, valid for ``ttl`` (S-11).

        Absolute rather than relative — unavoidably so, since the browser fetches
        it from the object store rather than from us. Which host it names is
        ``minio_signing_endpoint``'s decision; see the class note.

        ⚠️ **Both clocks matter.** The expiry is enforced by MinIO against *its*
        own clock, so a few minutes of drift against ours turns a 5-minute link
        into "expired the moment it was issued". NTP on both hosts is in the
        deployment checklist for exactly this reason (blind spot #3).
        """
        return self._signing_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=int(ttl.total_seconds()),
        )

    def delete(self, key: str) -> None:
        """Remove an object. Deleting one that is already gone is not an error —
        S3 says so, and the caller (``AttachmentService.delete``) has already
        removed the row, so a raise here would only strand a retry."""
        self._call("delete_object", Bucket=self._bucket, Key=key)

    # ---------------------------------------------------- operational helpers

    def head(self, key: str) -> dict:
        """Object metadata, for ``scripts/check_blob_store.py``. Raises
        ``FileNotFoundError`` when the object is not there."""
        return self._call("head_object", Bucket=self._bucket, Key=key)

    def ensure_bucket(self) -> None:
        """Create the bucket if it is absent. **Not called on the request path.**

        Who creates the bucket is blind spot #4, and the reason it is not done
        lazily at upload time is the second half of that spot: a bucket this
        adapter creates is private by default, but a bucket somebody else created
        may be anonymously readable — and anonymous read makes S-11's whole
        "permission-check, then sign for 5 minutes" mechanism decorative. So
        creation is an explicit operation used by the contract test and the smoke
        script, and the deployment checklist verifies the policy rather than
        trusting it.
        """
        from botocore.exceptions import ClientError

        try:
            self._call("head_bucket", Bucket=self._bucket)
        except FileNotFoundError:
            self._call("create_bucket", Bucket=self._bucket)
        except ClientError as exc:  # pragma: no cover - network-shaped failure
            raise BlobStoreUnavailable(str(exc)) from exc

    # ------------------------------------------------------------- internals

    def _read_up_to(self, stream: BinaryIO, limit: int, *, already: int) -> bytes:
        """Read at most ``limit`` bytes in ``CHUNK`` steps, enforcing the cap.

        This is semantic #1 from the module note. The check is inside the read
        loop, so an oversize upload is refused after the chunk that crosses the
        line rather than after the transfer completes.

        ``already`` is how many bytes of this object went up in earlier parts,
        and it is a **parameter rather than instance state** on purpose: the
        composition root caches one store for the whole process, so a counter on
        ``self`` would be shared by concurrent uploads and the limit would apply
        to whichever request happened to be reading.
        """
        buffer = bytearray()
        while len(buffer) < limit:
            chunk = stream.read(min(CHUNK, limit - len(buffer)))
            if not chunk:
                break
            buffer += chunk
            if already + len(buffer) > self._max_bytes:
                raise BlobTooLarge(f"attachment exceeds {self._max_bytes} bytes")
        return bytes(buffer)

    def _put_multipart(
        self, key: str, mime: str, first: bytes, stream: BinaryIO
    ) -> BlobRef:
        """Upload the parts, aborting on any failure.

        The abort is not politeness: an unfinished multipart upload keeps its
        parts, and they are billed and counted while being invisible to every
        listing the product does. Leaving them behind is how an object store
        fills up with data nobody can see.
        """
        created = self._call(
            "create_multipart_upload", Bucket=self._bucket, Key=key, ContentType=mime
        )
        upload_id = created["UploadId"]
        parts: list[dict] = []
        size = 0
        payload = first
        try:
            while payload:
                number = len(parts) + 1
                result = self._call(
                    "upload_part",
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=number,
                    Body=payload,
                )
                parts.append({"ETag": result["ETag"], "PartNumber": number})
                size += len(payload)
                payload = self._read_up_to(stream, PART_SIZE, already=size)
            self._call(
                "complete_multipart_upload",
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException:
            self._call(
                "abort_multipart_upload", Bucket=self._bucket, Key=key, UploadId=upload_id
            )
            raise
        return BlobRef(key=key, size=size, mime=mime)

    def _call(self, operation: str, **kwargs):
        """One place where S3 faults become this codebase's exceptions.

        Mapping them here rather than at the call sites is what lets
        ``AttachmentService`` treat both carriers identically: a missing object
        is a ``FileNotFoundError`` whether it was a file or an S3 key.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(self._client(), operation)(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
                if code == "NoSuchBucket":
                    raise BlobStoreUnavailable(
                        f"bucket {self._bucket!r} does not exist on {self._endpoint} — "
                        "create it (private, never anonymous-read) before deploying; "
                        "see markdown/relay-s1-deploy.md §O-5."
                    ) from exc
                raise FileNotFoundError(kwargs.get("Key", self._bucket)) from exc
            if code == "NoSuchBucket":
                raise BlobStoreUnavailable(
                    f"bucket {self._bucket!r} does not exist on {self._endpoint} — "
                    "create it (private, never anonymous-read) before deploying; "
                    "see markdown/relay-s1-deploy.md §O-5."
                ) from exc
            if status in {401, 403}:
                raise BlobStoreUnavailable(
                    "the object store refused our credentials "
                    "(RELAY_MINIO_ACCESS_KEY / RELAY_MINIO_SECRET_KEY)."
                ) from exc
            raise BlobStoreUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            # Connection refused, DNS failure, TLS. Not "the file is missing".
            raise BlobStoreUnavailable(f"{self._endpoint}: {exc}") from exc

    @functools.cached_property
    def _connection(self):
        return self._build_client(self._endpoint)

    @functools.cached_property
    def _signer(self):
        if self._public_endpoint == self._endpoint:
            return self._connection
        return self._build_client(self._public_endpoint)

    def _client(self):
        return self._connection

    def _signing_client(self):
        return self._signer

    def _build_client(self, endpoint: str):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            config=Config(
                # Blind spot #2: MinIO addresses buckets path-style. The SDK's
                # default is virtual-host, which fails as a NoSuchBucket or as a
                # DNS lookup of the bucket name as a subdomain.
                s3={"addressing_style": "path" if self._path_style else "virtual"},
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )
