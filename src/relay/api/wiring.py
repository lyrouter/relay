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


@lru_cache(maxsize=1)
def blob_store() -> FilesystemBlobStore:
    """LOG-5's carrier.

    The S1 carrier is self-hosted MinIO (F-4) and **its adapter is not written**
    — deliberately, since an adapter with no MinIO to test against is worse than
    an explicitly missing one (owner action O-5). What runs today is the
    filesystem store, in the same key layout, so switching carriers moves no
    object and rewrites no stored ``blob_key``.
    """
    return FilesystemBlobStore()


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
    for line in warnings:
        logger.warning(line)
    return warnings
