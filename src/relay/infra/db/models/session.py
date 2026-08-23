"""Login sessions (AC-2).

Not in the §4.1 entity list, and needed by two of its own requirements.

**Why server-side rather than a stateless token.** R-2 puts account
deactivation on a monthly review and on the offboarding checklist, because
without SSO nothing else revokes access. A self-contained token cannot be
revoked without a denylist — which is this table with extra steps and worse
semantics. So: sessions live in the database, and deactivating an account can
actually end them.

The row also carries where the login came from, which is what makes AC-2's
unfamiliar-location alert possible without a second table: "familiar" means an
earlier session for this user from the same network.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey, tenant_fk


class UserSession(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "user_session"
    __table_args__ = (
        tenant_fk("user_id", "user"),
        Index("ix_user_session_tenant_id_user_id_created_at", "tenant_id", "user_id", "created_at"),
        # The revocation sweep and the "end all sessions for this user" path.
        Index("ix_user_session_tenant_id_user_id_revoked_at", "tenant_id", "user_id", "revoked_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: Hash only, like every other bearer credential in the system.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    #: Two clocks, and both are needed. Idle expiry logs out a forgotten open
    #: tab; absolute expiry bounds a session someone keeps alive indefinitely by
    #: leaving a page polling.
    idle_expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why it ended — "logout" | "idle" | "absolute" | "password_change" |
    #: "deactivated" | "admin". Useful in exactly the investigation where
    #: guessing is expensive.
    revoked_reason: Mapped[str | None] = mapped_column(String(32))

    #: Where the login came from. Inside the tenant boundary and under policy,
    #: so this is the tenant's own security log, not cross-tenant data.
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    #: AC-3: whether TOTP was satisfied for this session. A session created
    #: before the second factor is not usable for anything else.
    mfa_satisfied: Mapped[bool] = mapped_column(nullable=False, default=True)
