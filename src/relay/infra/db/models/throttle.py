"""Signup and resend throttling (AC-1).

The one table in the model with no ``tenant_id``, and the reason is structural:
**a refused signup has no tenant.** Refusing an unknown domain is precisely the
case the IP limit exists to cover, so the counter has to live somewhere the
tenant boundary does not reach.

That would normally be a leak surface — a table with no policy is readable by
every tenant's connection. So it stores **no plaintext**: keys are SHA-256
digests of the IP or the email. A row says "this opaque key has tried 4 times",
which is all a limiter needs and nothing an attacker can enumerate without
already knowing the value.

In PostgreSQL rather than in memory, for the same reason §2.4 puts the webhook
queue there: a limit that resets when a worker restarts is not a limit, and S1
deliberately has no Redis.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from relay.infra.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Throttle(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "throttle"
    __table_args__ = (
        UniqueConstraint("bucket", "key_hash", name="uq_throttle_bucket_key_hash"),
        # Sweeping expired windows.
        Index("ix_throttle_window_started_at", "window_started_at"),
    )

    #: What is being limited: "signup_ip" | "signup_domain" | "verification_resend".
    bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SHA-256 hex of the subject. Never the subject itself.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    window_started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set once the limit trips, so a caller gets a retry-after without having
    #: to recompute the window.
    blocked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
