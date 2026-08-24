"""LOG-5 · S-25 · the blob contract, run against **both** carriers.

This file is the second of the three things S-25 asked for (adapter · contract
test · smoke script), and it is the one that makes writing the adapter blind
defensible: with no MinIO instance to develop against, a real ``minio/minio``
container is the only thing that can tell us the *semantics* are right.

**Be exact about what it does and does not prove.** It verifies the protocol —
key layout, the streaming size limit, presigned GET, error mapping, and that a
bucket we create is not anonymously readable. It cannot verify a deployment: not
the reverse-proxy endpoint a browser resolves, not the clocks, not the bucket
policy somebody else's bucket carries. That half belongs to
``scripts/check_blob_store.py`` and to the deployment checklist, and pretending a
container covers it is how "the tests pass" becomes "the images are all broken".

The container is skipped, never faked — and a skip has to be a decision rather
than an accident: with ``RELAY_REQUIRE_MINIO_CONTRACT=1`` (what CI sets) a
skipped MinIO half becomes a failure. For a blind-written adapter, a contract
test that quietly stops running is how it stays blind.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.context import tenant_scope
from relay.domain.enums import Role, UserStatus
from relay.infra.blob.filesystem import FilesystemBlobStore
from relay.infra.blob.minio import PART_SIZE, BlobStoreUnavailable, MinioBlobStore
from relay.ports.blob import BlobTooLarge, tenant_prefix

from .conftest import context_for, requires_db

#: An existing instance to test against, if the operator has one. Set this and
#: the container is not started at all — which is how the same suite becomes the
#: acceptance check for the *real* MinIO once O-5's instance exists.
ENDPOINT_ENV = "RELAY_TEST_MINIO_ENDPOINT"

#: Set in CI. Turns "the MinIO half was skipped" from a note into a failure.
REQUIRE_ENV = "RELAY_REQUIRE_MINIO_CONTRACT"

IMAGE = "minio/minio:latest"
ROOT_USER = "relaytest"
ROOT_PASSWORD = "relaytest-secret"
BUCKET = "relay-contract"

#: Why the MinIO half did not run, or None if it did. Module-level so the guard
#: test at the bottom can tell "skipped for a stated reason" from "quietly never
#: ran", which are very different things to see in CI output.
_skip_reason: str | None = None


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=30, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for(endpoint: str, timeout: float = 60.0) -> bool:
    """Poll MinIO's own liveness endpoint until it answers."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{endpoint}/minio/health/live", timeout=2) as answer:
                if answer.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def minio_endpoint() -> str:
    """A reachable S3 endpoint: an operator's, or a container we start.

    Session-scoped: starting MinIO costs a couple of seconds, and every test here
    works in its own key prefix, so sharing one instance is safe and honest —
    production shares one too.
    """
    global _skip_reason

    provided = os.environ.get(ENDPOINT_ENV)
    if provided:
        if not _wait_for(provided, timeout=10):
            _skip_reason = f"{ENDPOINT_ENV}={provided} is set but not answering"
            pytest.skip(_skip_reason)
        return provided.rstrip("/")

    if not _docker_available():
        _skip_reason = f"no usable docker; set {ENDPOINT_ENV} to point at a real MinIO"
        pytest.skip(_skip_reason)

    port = _free_port()
    name = f"relay-minio-contract-{port}"
    started = subprocess.run(
        [
            "docker", "run", "--rm", "-d", "--name", name,
            "-p", f"127.0.0.1:{port}:9000",
            "-e", f"MINIO_ROOT_USER={ROOT_USER}",
            "-e", f"MINIO_ROOT_PASSWORD={ROOT_PASSWORD}",
            IMAGE, "server", "/data",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        _skip_reason = f"could not start {IMAGE}: {started.stderr.strip()}"
        pytest.skip(_skip_reason)

    endpoint = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for(endpoint):
            _skip_reason = f"{IMAGE} started but never became live"
            pytest.skip(_skip_reason)
        yield endpoint
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.fixture(scope="session")
def minio(minio_endpoint) -> MinioBlobStore:
    store = MinioBlobStore(
        endpoint=minio_endpoint,
        public_endpoint=minio_endpoint,
        access_key=os.environ.get("RELAY_TEST_MINIO_ACCESS_KEY", ROOT_USER),
        secret_key=os.environ.get("RELAY_TEST_MINIO_SECRET_KEY", ROOT_PASSWORD),
        bucket=os.environ.get("RELAY_TEST_MINIO_BUCKET", BUCKET),
    )
    store.ensure_bucket()
    return store


@pytest.fixture
def filesystem(tmp_path) -> FilesystemBlobStore:
    return FilesystemBlobStore(root=str(tmp_path / "blobs"))


@pytest.fixture
def gateway():
    """A tenant, for the one test that drives the real use case (needs a database)."""
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway-blob",
            admin_email="admin@zerosone.test",
            admin_password="Corr3ct-Horse-Battery",
        )
    )


@pytest.fixture
def author(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "author@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )


def as_user(gateway, user_id):
    return tenant_scope(context_for(gateway.tenant_id, user_id))


@pytest.fixture(params=["filesystem", "minio"])
def carrier(request):
    """Both carriers, one contract.

    Parametrised rather than duplicated because the whole point of the key layout
    living in ``relay.ports.blob`` is that these assertions have one wording. A
    difference between the two here is a difference that would move objects
    during the carrier switch.
    """
    return request.getfixturevalue(request.param)


# ------------------------------------------------- the contract, both carriers


def test_the_key_carries_the_tenant_and_the_filename(carrier):
    """Mechanic #1 of S-11: a key minted for one tenant cannot name an object in
    another, because the tenant id is *in* the key by construction."""
    tenant = uuid.uuid4()
    ref = carrier.put(tenant, "screen shot.png", "image/png", io.BytesIO(b"bytes"))

    assert ref.key.startswith(tenant_prefix(tenant))
    assert ref.key.endswith("/screen shot.png")
    # t / <tenant> / <random> / <name>: the random segment is what stops two
    # uploads of the same name colliding, and stops a key being guessed from a
    # filename somebody mentioned in a ticket.
    assert len(ref.key.split("/")) == 4
    assert ref.size == 5
    assert ref.mime == "image/png"


def test_two_uploads_of_one_filename_do_not_collide(carrier):
    tenant = uuid.uuid4()
    first = carrier.put(tenant, "screenshot.png", "image/png", io.BytesIO(b"one"))
    second = carrier.put(tenant, "screenshot.png", "image/png", io.BytesIO(b"two!"))
    assert first.key != second.key
    assert (first.size, second.size) == (3, 4)


def test_a_traversal_filename_cannot_escape_the_layout(carrier):
    """``safe_filename`` is the port's, so both carriers apply it identically —
    and the *stored* name is sanitised too, because it ends up in a
    ``Content-Disposition`` header."""
    ref = carrier.put(uuid.uuid4(), "../../etc/passwd", "text/plain", io.BytesIO(b"x"))
    assert ref.key.endswith("/passwd")
    assert ".." not in ref.key


def test_the_size_limit_is_enforced_while_streaming(carrier, tmp_path, request):
    """S-25's semantic #1, asserted on both carriers.

    A 25 MiB cap that is checked after the transfer still refuses the file and no
    longer protects bandwidth or disk. ``BlobTooLarge`` is the port's exception
    for exactly that reason, so the wording of this test is the same for a
    directory and for an object store.
    """
    if isinstance(carrier, FilesystemBlobStore):
        tight = FilesystemBlobStore(root=str(tmp_path / "tight"), max_bytes=32)
    else:
        # Asked for lazily: requesting the endpoint as a parameter would start a
        # container for the filesystem half too, and skip it when docker is absent.
        tight = MinioBlobStore(
            endpoint=request.getfixturevalue("minio_endpoint"),
            public_endpoint=minio_endpoint,
            access_key=ROOT_USER,
            secret_key=ROOT_PASSWORD,
            bucket=BUCKET,
            max_bytes=32,
        )

    class CountingStream(io.RawIOBase):
        """Reports how much was actually pulled off the client."""

        def __init__(self, payload: bytes) -> None:
            self._inner = io.BytesIO(payload)
            self.read_bytes = 0

        def read(self, size: int = -1) -> bytes:
            chunk = self._inner.read(size)
            self.read_bytes += len(chunk)
            return chunk

    stream = CountingStream(b"0" * (1024 * 1024))
    with pytest.raises(BlobTooLarge):
        tight.put(uuid.uuid4(), "big.bin", "application/zip", stream)

    # The cost of the refusal is one chunk past the limit, not the whole file.
    assert stream.read_bytes <= 32 + 64 * 1024


def test_deleting_twice_is_not_an_error(carrier):
    """The caller deletes the row first, so a raise here would only strand a
    retry on an object that is already gone."""
    ref = carrier.put(uuid.uuid4(), "gone.txt", "text/plain", io.BytesIO(b"bye"))
    carrier.delete(ref.key)
    carrier.delete(ref.key)


def test_a_signed_link_is_produced_for_the_stored_key(carrier):
    ref = carrier.put(uuid.uuid4(), "shot.png", "image/png", io.BytesIO(b"png"))
    url = carrier.signed_url(ref.key)
    # Both carriers name the key and carry a signature and an expiry; *where* the
    # URL points is the carrier's business and is asserted per carrier below.
    assert ref.key.rsplit("/", 1)[-1] in url
    assert "sig" in url.lower() or "signature" in url.lower()


# ------------------------------------------------------ MinIO-specific mechanics


def test_a_presigned_link_actually_serves_the_bytes(minio):
    """The round trip the smoke script performs on a real instance: put →
    presign → GET. If this passes and production fails, the difference is
    deployment shape, which is the whole content of the S-25 blind-spot table."""
    payload = b"\x89PNG relay contract"
    ref = minio.put(uuid.uuid4(), "round trip.png", "image/png", io.BytesIO(payload))

    with urllib.request.urlopen(minio.signed_url(ref.key), timeout=10) as answer:
        assert answer.status == 200
        assert answer.read() == payload

    minio.delete(ref.key)


def test_an_unsigned_request_is_refused(minio):
    """Blind spot #4, the half a container *can* check: the bucket must not be
    anonymously readable. If it is, S-11's "authorize, then sign for 5 minutes"
    is decorative — anybody with the key reads the object forever."""
    ref = minio.put(uuid.uuid4(), "private.txt", "text/plain", io.BytesIO(b"secret"))
    unsigned = minio.signed_url(ref.key).split("?")[0]

    with pytest.raises(urllib.error.HTTPError) as refusal:
        urllib.request.urlopen(unsigned, timeout=10)
    assert refusal.value.code in (401, 403)


def test_an_expired_link_stops_working(minio):
    """The link is not the credential — it is a five-minute capability. Zero
    seconds is the same mechanism with the clock already past."""
    import datetime as dt

    ref = minio.put(uuid.uuid4(), "brief.txt", "text/plain", io.BytesIO(b"brief"))
    url = minio.signed_url(ref.key, ttl=dt.timedelta(seconds=1))
    time.sleep(2)

    with pytest.raises(urllib.error.HTTPError) as refusal:
        urllib.request.urlopen(url, timeout=10)
    assert refusal.value.code == 403


def test_an_object_larger_than_one_part_round_trips(minio):
    """The multipart path, which is where a naive port silently changes the size
    semantics. One part plus a bit, so both branches of ``_put_multipart`` run."""
    payload = os.urandom(PART_SIZE + 1024)
    ref = minio.put(uuid.uuid4(), "heap.bin", "application/zip", io.BytesIO(payload))

    assert ref.size == len(payload)
    with urllib.request.urlopen(minio.signed_url(ref.key), timeout=60) as answer:
        assert answer.read() == payload
    minio.delete(ref.key)


def test_a_missing_object_is_a_file_not_found(minio):
    """Error mapping: the use case must not have to know which carrier it has."""
    with pytest.raises(FileNotFoundError):
        minio.head(f"{tenant_prefix(uuid.uuid4())}nope/absent.txt")


def test_a_missing_bucket_names_itself(minio_endpoint):
    """A misconfigured bucket is the second most likely first bug, so the message
    has to say which bucket and where — not ``ClientError``."""
    wrong = MinioBlobStore(
        endpoint=minio_endpoint,
        public_endpoint=minio_endpoint,
        access_key=ROOT_USER,
        secret_key=ROOT_PASSWORD,
        bucket="relay-does-not-exist",
    )
    with pytest.raises(BlobStoreUnavailable) as failure:
        wrong.put(uuid.uuid4(), "x.txt", "text/plain", io.BytesIO(b"x"))
    assert "relay-does-not-exist" in str(failure.value)


def test_bad_credentials_are_not_reported_as_a_missing_file(minio_endpoint):
    denied = MinioBlobStore(
        endpoint=minio_endpoint,
        public_endpoint=minio_endpoint,
        access_key="wrong",
        secret_key="wrong-secret-value",
        bucket=BUCKET,
    )
    with pytest.raises(BlobStoreUnavailable) as failure:
        denied.put(uuid.uuid4(), "x.txt", "text/plain", io.BytesIO(b"x"))
    assert "RELAY_MINIO_ACCESS_KEY" in str(failure.value)


def test_an_unreachable_endpoint_is_unavailable_not_missing(tmp_path):
    """No container needed: connection refused must not look like "no such
    object", because the two have opposite operational answers."""
    nowhere = MinioBlobStore(
        endpoint=f"http://127.0.0.1:{_free_port()}",
        access_key="a",
        secret_key="bbbbbbbbbbbbbbbb",
        bucket=BUCKET,
    )
    with pytest.raises(BlobStoreUnavailable):
        nowhere.put(uuid.uuid4(), "x.txt", "text/plain", io.BytesIO(b"x"))


# ------------------------------------- the deployment shape, without an instance


def test_the_signed_link_uses_the_public_endpoint():
    """**Blind spot #1**, and the one that fails most quietly in production.

    A presigned URL embeds the endpoint of the client that signed it. MinIO is
    normally "internal endpoint + reverse-proxied public name", so signing with
    the connection endpoint hands the browser an address it cannot resolve — and
    nothing appears in our log, because the browser never talks to us. No
    container required to check it: the host is in the string.
    """
    store = MinioBlobStore(
        endpoint="http://minio.internal:9000",
        public_endpoint="https://files.relay.example",
        access_key="key",
        secret_key="secret-secret-secret",
        bucket="relay-attachments",
    )
    url = store.signed_url("t/abc/def/shot.png")

    assert url.startswith("https://files.relay.example/")
    assert "minio.internal" not in url


def test_addressing_is_path_style():
    """**Blind spot #2**. MinIO addresses buckets path-style; an SDK default of
    virtual-host turns into ``NoSuchBucket`` or a DNS lookup for
    ``relay-attachments.minio.internal``."""
    store = MinioBlobStore(
        endpoint="http://minio.internal:9000",
        access_key="key",
        secret_key="secret-secret-secret",
        bucket="relay-attachments",
    )
    url = store.signed_url("t/abc/def/shot.png")

    assert url.startswith("http://minio.internal:9000/relay-attachments/t/abc/def/shot.png")
    assert "relay-attachments.minio.internal" not in url


def test_the_ttl_reaches_the_signature():
    store = MinioBlobStore(
        endpoint="http://minio.internal:9000",
        access_key="key",
        secret_key="secret-secret-secret",
        bucket="relay-attachments",
    )
    import datetime as dt

    assert "X-Amz-Expires=300" in store.signed_url(
        "t/a/b/c.png", ttl=dt.timedelta(minutes=5)
    )


def test_the_s3_carrier_has_no_local_delivery_methods():
    """S-25's semantic #2, asserted rather than described.

    ``/blobs/{key}`` needs ``verify`` and ``open``; S3 has no equivalent, so the
    adapter deliberately does not have them and the route is not mounted for this
    carrier (``relay.api.wiring.blob_delivery_is_local``). A stub that existed
    only to keep the import working is what this test forbids.
    """
    assert not hasattr(MinioBlobStore, "verify")
    assert not hasattr(MinioBlobStore, "open")


def test_an_empty_endpoint_refuses_to_construct():
    """Rather than defaulting to something local: "attachments quietly went to a
    different store" is not a failure anybody notices in time."""
    with pytest.raises(BlobStoreUnavailable):
        MinioBlobStore(endpoint="", access_key="a", secret_key="b", bucket="c")


# ------------------------------------ the product path, on the deployed carrier


@requires_db
@pytest.mark.db
def test_the_attachment_path_works_on_the_s3_carrier(minio, gateway, author):
    """The point of the whole exercise: LOG-5's use case, unchanged, on MinIO.

    ``AttachmentService`` is carrier-agnostic by construction, but "by
    construction" is a claim until something runs it — and the two things most
    likely to break are the ones this asserts: the link is **absolute** (the
    browser goes to the object store, not to us) and nothing in the path calls
    ``verify``/``open``, which this carrier does not have.
    """
    from relay.app.logs.attachments import AttachmentService
    from relay.app.logs.service import LogService

    with as_user(gateway, author):
        log = LogService().create("排查记录", "带一张图")
        view = AttachmentService(minio).upload(
            "log", log.id, "screen.png", "image/png", io.BytesIO(b"\x89PNG relay")
        )
        assert view.size == len(b"\x89PNG relay")
        assert view.scan_state == "skipped"

        url = AttachmentService(minio).link(view.id)

    assert url.startswith("http")  # absolute: the object store serves it
    with urllib.request.urlopen(url, timeout=10) as answer:
        assert answer.read() == b"\x89PNG relay"


def test_the_local_delivery_route_is_gone_under_the_s3_carrier(monkeypatch):
    """S-25's semantic #2, at the level where it matters: the URL space.

    Under MinIO ``/blobs/{key}`` cannot work — so it must not exist. A mounted
    route answering ``AttributeError`` would be a 500 on every image, discovered
    by a user rather than by this assertion. Asked via ``url_path_for`` because
    that is the question being posed: is this route reachable at all?
    """
    from starlette.routing import NoMatchFound

    from relay.api import wiring
    from relay.api.app import create_app
    from relay.config import settings

    monkeypatch.setattr(settings, "blob_carrier", "filesystem")
    wiring.reset()
    assert create_app().url_path_for("serve_blob", key="t/a/b/c.png") == "/blobs/t/a/b/c.png"

    monkeypatch.setattr(settings, "blob_carrier", "minio")
    wiring.reset()
    with pytest.raises(NoMatchFound):
        create_app().url_path_for("serve_blob", key="t/a/b/c.png")
    wiring.reset()


# ------------------------------------------------------------------- the guard


def test_the_minio_half_is_not_silently_absent():
    """A skipped container half has to be a *decision*, not an accident.

    Locally, no docker means these tests skip and say so. In CI it must not be
    possible to lose them by breaking the image pull: set
    ``RELAY_REQUIRE_MINIO_CONTRACT=1`` there and a skip becomes a failure. This
    is the same discipline ``tests/test_ci_gates.py`` applies to the RLS suite —
    for a blind-written adapter, a silently skipped contract test is how it stays
    blind.
    """
    if os.environ.get(REQUIRE_ENV) not in {"1", "true", "yes"}:
        pytest.skip(f"set {REQUIRE_ENV}=1 to make a missing MinIO a failure")
    assert _skip_reason is None, f"MinIO contract half did not run: {_skip_reason}"
