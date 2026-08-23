"""Rate limiting for the account flows (AC-1).

Two limits, and they defend against different things:

* **per IP** — someone walking a domain list to find which ones are allowlisted.
  Refusals are the interesting signal to an attacker, so refused attempts must
  count too, which is why this cannot live inside the tenant boundary.
* **per email** — resend flooding. Someone else's mailbox is the target here,
  not our database, so the cooldown is deliberately longer than it needs to be
  for load reasons.

Keys are hashed before they reach the database (see ``models/throttle.py``).
Counting uses a fixed window rather than a sliding one: at this scale the extra
precision buys nothing, and a fixed window is one row and one UPDATE.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.app.errors import RateLimited
from relay.infra.db.models import Throttle


@dataclass(frozen=True, slots=True)
class Limit:
    bucket: str
    max_attempts: int
    window: dt.timedelta
    #: How long the caller is blocked once the limit trips. Longer than the
    #: window, so hammering does not simply reset into the next one.
    block_for: dt.timedelta
    message: str


#: Generous enough that a team onboarding together never sees it, tight enough
#: that domain enumeration is not free.
SIGNUP_PER_IP = Limit(
    bucket="signup_ip",
    max_attempts=10,
    window=dt.timedelta(hours=1),
    block_for=dt.timedelta(hours=1),
    message="注册尝试过于频繁，请稍后再试。",
)

SIGNUP_PER_DOMAIN = Limit(
    bucket="signup_domain",
    max_attempts=50,
    window=dt.timedelta(hours=1),
    block_for=dt.timedelta(hours=1),
    message="该域名下的注册尝试过于频繁，请稍后再试。",
)

#: The subject here is somebody else's inbox, so this is about not being used as
#: a mail cannon rather than about our own load.
VERIFICATION_RESEND = Limit(
    bucket="verification_resend",
    max_attempts=3,
    window=dt.timedelta(minutes=15),
    block_for=dt.timedelta(minutes=15),
    message="验证邮件发送过于频繁，请查收邮箱或 15 分钟后再试。",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def check_and_consume(
    session: Session, limit: Limit, subject: str, *, now: dt.datetime | None = None
) -> None:
    """Count one attempt against ``subject``; raise ``RateLimited`` if over.

    Consumes on *every* call, including ones that go on to fail. That is
    intentional: a limiter that only counts successes does not limit anything.
    """
    now = now or dt.datetime.now(dt.UTC)
    key_hash = _digest(subject)

    row = session.scalars(
        select(Throttle)
        .where(Throttle.bucket == limit.bucket, Throttle.key_hash == key_hash)
        .with_for_update()
    ).first()

    if row is None:
        session.add(
            Throttle(
                bucket=limit.bucket, key_hash=key_hash, attempts=1, window_started_at=now
            )
        )
        session.flush()
        return

    if row.blocked_until and row.blocked_until > now:
        raise RateLimited(
            limit.message,
            retry_after_seconds=int((row.blocked_until - now).total_seconds()),
        )

    if now - row.window_started_at >= limit.window:
        row.window_started_at = now
        row.attempts = 1
        row.blocked_until = None
        session.flush()
        return

    row.attempts += 1
    if row.attempts > limit.max_attempts:
        row.blocked_until = now + limit.block_for
        session.flush()
        raise RateLimited(
            limit.message, retry_after_seconds=int(limit.block_for.total_seconds())
        )
    session.flush()


def reset(session: Session, limit: Limit, subject: str) -> None:
    """Clear a counter. Used after a *successful* verification, so that finishing
    the flow does not leave the user throttled on their next legitimate action."""
    row = session.scalars(
        select(Throttle).where(
            Throttle.bucket == limit.bucket, Throttle.key_hash == _digest(subject)
        )
    ).first()
    if row is not None:
        session.delete(row)
        session.flush()
