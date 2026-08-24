"""The composition root: where ports meet the adapters behind them.

Every ``*_port()`` here is a function rather than a module-level object so that a
deployment's configuration is read when the first request needs it, not at import
time — which is also what lets a test point ``RELAY_BLOB_ROOT`` somewhere else
without reloading the package.

The one thing this module says out loud: **an empty setting selects the null
adapter, and that is a visible choice rather than a silent one.** ``RELAY_SMTP_HOST``
unset means ``NullMailPort``, which records mail instead of sending it — so email
verification appears to work and nobody can finish signing up (owner action O-2).
The log line below is the difference between finding that in five minutes and
finding it in a day.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from relay.config import settings
from relay.infra.blob.filesystem import FilesystemBlobStore
from relay.infra.blob.minio import MinioBlobStore
from relay.infra.mail.smtp import SmtpMailPort
from relay.infra.search import PgroongaSearch
from relay.ports.mail import MailPort, NullMailPort

logger = logging.getLogger("relay.api")


@lru_cache(maxsize=1)
def mail_port() -> MailPort:
    """SMTP when configured, otherwise the recorder.

    Three transactional paths depend on this and one of them gates every account:
    email verification (AC-1), the unfamiliar-network alert (AC-2), and
    invitations. None of them is a *notification* — F-1's "in-app only" decision
    does not apply to them, which is why they send at all.
    """
    if not settings.smtp_host:
        logger.warning(
            "RELAY_SMTP_HOST is unset: mail is recorded, not sent. Email verification "
            "cannot complete, so nobody can finish signing up (owner action O-2)."
        )
        return NullMailPort()
    return SmtpMailPort(
        settings.smtp_host,
        settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        use_starttls=settings.smtp_use_starttls,
        sender=settings.mail_sender,
    )


#: The carriers this deployment can select between. Named as a constant so an
#: unknown value is a startup error listing the valid ones, rather than a silent
#: fall back to the filesystem — "attachments are on local disk in production" is
#: not a mistake anything else would ever report.
BLOB_CARRIERS = ("filesystem", "minio")


@lru_cache(maxsize=1)
def blob_store() -> FilesystemBlobStore | MinioBlobStore:
    """LOG-5's carrier — **the one place it is chosen** (S-25).

    S1's deployed carrier is self-hosted MinIO (F-4). The adapter was written
    blind against standard S3 semantics rather than waiting for an instance
    (S-25, formerly owner action O-5), so what used to be a code blocker is now
    three settings and a smoke test.

    ``filesystem`` stays the development and test carrier, in the *same* key
    layout — so switching moves no object and rewrites no stored ``blob_key``.

    **Why the switch is visible here and not per call site.** The carrier changes
    more than a class: with MinIO the signed link points at the object store, so
    ``/blobs/{key}`` — which needs the filesystem carrier's ``verify`` and
    ``open`` — has no meaning and must not be mounted. See
    :func:`blob_delivery_is_local`, and ``relay.api.app``, where that answer
    decides whether the route exists. A store picked lazily inside a route would
    leave that route registered and answering ``AttributeError``.
    """
    carrier = (settings.blob_carrier or "filesystem").strip().lower()
    if carrier not in BLOB_CARRIERS:
        raise ValueError(
            f"RELAY_BLOB_CARRIER={settings.blob_carrier!r} is not one of {BLOB_CARRIERS}."
        )
    if carrier == "minio":
        return MinioBlobStore()
    return FilesystemBlobStore()


def blob_delivery_is_local() -> bool:
    """Whether *this* application serves attachment bytes.

    True for the filesystem carrier, where a signed link comes back to
    ``/blobs/{key}``; false for MinIO, where the browser fetches the object
    directly and never reaches us. ``relay.api.app`` asks before mounting the
    route — the alternative is a route that exists and cannot work, which is the
    ``AttributeError`` S-25 named as one of the two semantics to align up front.
    """
    return (settings.blob_carrier or "filesystem").strip().lower() != "minio"


@lru_cache(maxsize=1)
def search_port() -> PgroongaSearch:
    """LOG-8. PG FTS + pgroonga, same database, same policies (F-2)."""
    return PgroongaSearch()


def reset() -> None:
    """Drop the cached adapters. For tests that change configuration."""
    mail_port.cache_clear()
    blob_store.cache_clear()
    search_port.cache_clear()


#: The value that means "nobody set this". Compared by identity of the string
#: rather than by a flag so there is one definition of "unset" (config.py's
#: default) and no second place to update.
DEV_SIGNING_KEY = "dev-only-unsafe-signing-key"
DEV_WEBHOOK_KEY = "dev-only-unsafe-webhook-key"


def check_configuration() -> list[str]:
    """Log the settings that make the application quietly wrong. Returns them.

    Run at startup, because "visible in a config review" (config.py's promise
    about the signing key) only holds if something actually says it out loud.
    Every entry here is a deployment mistake that leaves the software *working* —
    which is why they need announcing rather than raising:

    * no SMTP means verification mail is recorded and not sent, so nobody can
      finish signing up, and the failure looks like "the email never arrived";
    * the default signing key means attachment links are signed with a value
      published in this repository;
    * a non-Secure session cookie is correct for local http and wrong everywhere
      else, and nothing about the running system looks different.

    Returned as a list so a test can assert the check still fires — a startup
    warning nobody has ever seen fail is the same as no check.
    """
    warnings: list[str] = []
    if not settings.smtp_host:
        warnings.append(
            "RELAY_SMTP_HOST is unset: mail is recorded, not sent. Email verification "
            "cannot complete, so nobody can finish signing up (owner action O-2)."
        )
    if settings.webhook_signing_key == DEV_WEBHOOK_KEY:
        warnings.append(
            "RELAY_WEBHOOK_SIGNING_KEY is still the development default, which is "
            "public: anybody could forge a signed webhook delivery. Generate one per "
            "environment: openssl rand -hex 32."
        )
    if settings.blob_signing_key == DEV_SIGNING_KEY:
        warnings.append(
            "RELAY_BLOB_SIGNING_KEY is still the development default, which is public. "
            "Generate one per environment: openssl rand -hex 32 (owner action O-1)."
        )
    if not settings.session_cookie_secure:
        warnings.append(
            "RELAY_SESSION_COOKIE_SECURE is off: the session cookie will travel over "
            "http. Correct for local development, wrong for any deployment."
        )
    warnings.extend(_blob_carrier_warnings())
    for line in warnings:
        logger.warning(line)
    return warnings


def _blob_carrier_warnings() -> list[str]:
    """S-25's two deployment-shape traps, said out loud at startup.

    Both leave the software running: the filesystem carrier serves attachments
    happily from a container's local disk (and loses them on the next deploy),
    and a MinIO carrier signing links against its internal endpoint produces
    **broken images with nothing in this log** — the browser talks to the object
    store directly, so the failure never reaches the application at all. A
    warning here is the only place either becomes visible before a user finds it.
    """
    carrier = (settings.blob_carrier or "filesystem").strip().lower()
    if carrier != "minio":
        return [
            "RELAY_BLOB_CARRIER is 'filesystem': attachments are stored on local disk. "
            "S1's carrier is self-hosted MinIO (F-4) — set RELAY_BLOB_CARRIER=minio "
            "before deploying, or attachments do not survive a redeploy."
        ]
    lines = []
    if not settings.minio_public_endpoint:
        lines.append(
            "RELAY_MINIO_PUBLIC_ENDPOINT is unset: download links will be signed for "
            f"{settings.minio_endpoint!r}. Correct only if browsers can reach that "
            "address — otherwise every image breaks with nothing in this log (S-25)."
        )
    if not settings.minio_access_key or not settings.minio_secret_key:
        lines.append(
            "RELAY_MINIO_ACCESS_KEY / RELAY_MINIO_SECRET_KEY are incomplete: uploads "
            "will fail on the first attachment."
        )
    return lines
