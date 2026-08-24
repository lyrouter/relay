"""LOG-5 · S-25 · one round trip against the *real* object store.

    uv run python scripts/check_blob_store.py
    uv run python scripts/check_blob_store.py --keep   # leave the probe object

**Why this exists.** The MinIO adapter was written blind (S-25): its semantics are
verified against a container in ``tests/test_blob_contract.py``, but a container
cannot verify a *deployment* — which endpoint a browser resolves, who created the
bucket and whether it is anonymously readable, whether the two clocks agree. This
script makes the real instance prove those on day one. Without it, the first
symptom of any of them is every image in the product being broken during the
launch window, with **nothing in the application log**, because the browser talks
to the object store directly and never comes back to us.

It performs the whole path a user's attachment takes — put → presign → GET →
delete — and then checks the three deployment-shape traps the blind write could
not cover. Run it:

* once after configuring ``RELAY_MINIO_*`` (deployment checklist §O-5);
* again from a host that reaches the app the way a browser does, because the
  presigned host is the thing most likely to differ between the two;
* after any change to the reverse proxy in front of MinIO.

Exit status is 0 when every check passed, 1 otherwise, so it can be a step in a
deploy script rather than something somebody remembers to eyeball.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import io
import os
import sys
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

from relay.api import wiring
from relay.config import settings
from relay.infra.blob.minio import BlobStoreUnavailable, MinioBlobStore

#: Small, obviously-a-probe content. PNG magic so the MIME allowlist and any
#: content sniffing on the way through a proxy behave like they will in the product.
PROBE = b"\x89PNG\r\n\x1a\n relay blob store probe"

OK = "  ok   "
FAIL = " FAIL  "
WARN = " warn  "


class Report:
    """Collects results so the run prints every finding rather than the first.

    Deliberately not fail-fast: a deployment with two problems should be fixed in
    one pass, and "the presigned host is wrong" is exactly the finding that hides
    behind an earlier failure.
    """

    def __init__(self) -> None:
        self.failed = False

    def ok(self, line: str) -> None:
        print(f"[{OK}] {line}")

    def warn(self, line: str) -> None:
        print(f"[{WARN}] {line}")

    def fail(self, line: str) -> None:
        self.failed = True
        print(f"[{FAIL}] {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Round-trip the configured blob store.")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="do not delete the probe object (leaves it for manual inspection)",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="tenant id to use in the probe key; defaults to a random one",
    )
    args = parser.parse_args()
    report = Report()

    carrier = (settings.blob_carrier or "filesystem").strip().lower()
    print(f"carrier: {carrier}")
    if carrier != "minio":
        report.fail(
            "RELAY_BLOB_CARRIER is not 'minio', so this checks nothing about the "
            "deployed carrier. Set it (deployment checklist §O-5) and re-run."
        )
        return 1

    print(f"endpoint (application): {settings.minio_endpoint}")
    print(f"endpoint (signed links): {settings.minio_signing_endpoint}")
    print(f"bucket: {settings.minio_bucket}")
    print("")

    try:
        store = wiring.blob_store()
    except (BlobStoreUnavailable, ValueError) as exc:
        report.fail(f"the store could not be constructed: {exc}")
        return 1
    assert isinstance(store, MinioBlobStore)

    tenant = uuid.UUID(args.tenant) if args.tenant else uuid.uuid4()
    key = _put(store, tenant, report)
    if key is None:
        return 1

    url = _presign(store, key, report)
    if url is not None:
        _get(url, report)
        _anonymous(url, report)
    _clock(store, key, report)

    if args.keep:
        report.warn(f"probe object left in place: {key}")
    else:
        try:
            store.delete(key)
            report.ok("delete: the probe object was removed")
        except BlobStoreUnavailable as exc:
            report.fail(f"delete failed, leaving {key} behind: {exc}")

    print("")
    print("FAILED — see above" if report.failed else "all checks passed")
    return 1 if report.failed else 0


def _put(store: MinioBlobStore, tenant: uuid.UUID, report: Report) -> str | None:
    try:
        ref = store.put(tenant, "relay-probe.png", "image/png", io.BytesIO(PROBE))
    except BlobStoreUnavailable as exc:
        report.fail(f"upload failed: {exc}")
        return None
    if ref.size != len(PROBE):
        report.fail(f"upload reported {ref.size} bytes for a {len(PROBE)}-byte object")
        return ref.key
    report.ok(f"upload: {ref.size} bytes at {ref.key}")
    return ref.key


def _presign(store: MinioBlobStore, key: str, report: Report) -> str | None:
    try:
        url = store.signed_url(key, ttl=dt.timedelta(seconds=settings.blob_link_ttl_seconds))
    except BlobStoreUnavailable as exc:  # pragma: no cover - signing is local
        report.fail(f"could not sign a link: {exc}")
        return None

    host = urlsplit(url).netloc
    connection_host = urlsplit(settings.minio_endpoint).netloc
    if host == connection_host and not settings.minio_public_endpoint:
        # Blind spot #1. Not a failure — it is correct when the internal address
        # is also the public one — but it is the single most common cause of
        # "every image is broken and the log is empty", so it gets said out loud.
        report.warn(
            f"signed links point at {host}, the same address the application "
            "connects to. Correct only if browsers can reach it; otherwise set "
            "RELAY_MINIO_PUBLIC_ENDPOINT."
        )
    else:
        report.ok(f"signed links point at {host}")

    if f"/{settings.minio_bucket}/" not in url:
        # Blind spot #2: virtual-host addressing would put the bucket in the
        # hostname, which MinIO does not serve and DNS usually cannot resolve.
        report.fail(
            "the signed URL is not path-style — the bucket is not in the path. "
            "Set RELAY_MINIO_PATH_STYLE=true."
        )
    else:
        report.ok("addressing is path-style")
    return url


def _get(url: str, report: Report) -> None:
    """Follow the link exactly as a browser would."""
    try:
        with urllib.request.urlopen(url, timeout=15) as answer:
            body = answer.read()
            served_at = answer.headers.get("Date")
    except urllib.error.HTTPError as exc:
        report.fail(
            f"the signed link was refused with HTTP {exc.code}. If it is 403, the "
            "usual causes are clock skew between this host and MinIO, or a link "
            f"whose {settings.blob_link_ttl_seconds}s TTL already elapsed."
        )
        return
    except (urllib.error.URLError, OSError) as exc:
        report.fail(
            f"the signed link could not be fetched from this host: {exc}. This is "
            "what a browser will see; check the public endpoint and its proxy."
        )
        return

    if body != PROBE:
        report.fail(f"the object came back changed: {len(body)} bytes, expected {len(PROBE)}")
        return
    report.ok("signed GET: the bytes came back intact")
    _skew(served_at, report)


def _anonymous(url: str, report: Report) -> None:
    """Blind spot #4: the bucket must be **private**.

    An anonymously readable bucket makes S-11's "permission-check, then sign for
    five minutes" decorative — the key is in the database, in log bodies and in
    browser history, and any of those becomes a permanent capability.
    """
    unsigned = url.split("?")[0]
    try:
        with urllib.request.urlopen(unsigned, timeout=15):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            report.ok("the bucket refuses unsigned reads (it is private)")
            return
        report.warn(f"unsigned read answered HTTP {exc.code}; expected 401 or 403")
        return
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover
        report.warn(f"could not test the unsigned read: {exc}")
        return
    report.fail(
        "THE BUCKET IS ANONYMOUSLY READABLE. Every attachment is public to anyone "
        "who learns a key, and the permission check in front of the signed link "
        "buys nothing. Make the bucket private before going live (S-11)."
    )


def _skew(served_at: str | None, report: Report) -> None:
    """Blind spot #3: a five-minute link and two clocks that disagree.

    MinIO enforces the expiry against its own clock, so a few minutes of drift
    means "the link expired the moment it was issued". The store's ``Date``
    header is the cheapest way to see the difference.
    """
    if not served_at:
        report.warn("the store sent no Date header, so clock skew was not checked")
        return
    try:
        theirs = email.utils.parsedate_to_datetime(served_at)
    except (TypeError, ValueError):  # pragma: no cover
        report.warn(f"could not parse the store's Date header: {served_at!r}")
        return
    drift = abs((dt.datetime.now(dt.UTC) - theirs).total_seconds())
    if drift > 60:
        report.fail(
            f"the clocks differ by {int(drift)}s. A {settings.blob_link_ttl_seconds}s "
            "signed link cannot survive that — run NTP on both hosts."
        )
    elif drift > 15:
        report.warn(f"the clocks differ by {int(drift)}s; keep NTP running on both hosts")
    else:
        report.ok(f"clocks agree within {int(drift)}s")


def _clock(store: MinioBlobStore, key: str, report: Report) -> None:
    """Confirm the object is really there, via a signed metadata read."""
    try:
        store.head(key)
    except FileNotFoundError:
        report.fail(f"the object we just uploaded is not there: {key}")
    except BlobStoreUnavailable as exc:
        report.fail(f"metadata read failed: {exc}")
    else:
        report.ok("metadata read: the object is present")


if __name__ == "__main__":
    # Fail loudly rather than printing a traceback that reads like a bug in the
    # script when it is a fact about the deployment.
    if os.environ.get("RELAY_BLOB_CARRIER") is None and not settings.minio_endpoint:
        print(
            "RELAY_MINIO_ENDPOINT is empty. This script checks a real object store; "
            "configure it first (markdown/relay-s1-deploy.md §O-5).",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(main())
