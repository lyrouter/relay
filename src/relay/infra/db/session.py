"""Session factories and the ``SET LOCAL`` injection (MT-3, detail 2).

The listener below is the single place the ``TenantContext`` becomes a database
fact. It is bound to ``TenantSession`` only — ``SystemSession`` deliberately has
no listener, because it runs on the BYPASSRLS role.

Why ``set_config(..., true)`` and not ``SET LOCAL``: they do the same thing, but
``set_config`` takes a bind parameter, so the tenant id never reaches the server
as interpolated SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

from relay.context import (
    MissingTenantContext,
    TenantContext,
    current_context_or_none,
    tenant_scope,
)
from relay.infra.db.engine import app_engine, system_engine
from relay.infra.db.rls import TENANT_GUC


class TenantSession(Session):
    """A session that refuses to begin a transaction without a tenant."""


class SystemSession(Session):
    """The BYPASSRLS session. Not for serving requests."""


class TenantContextSwitchError(RuntimeError):
    """A single session must not straddle two tenants.

    Without this check, a session that begins under tenant A, commits, and then
    begins again under tenant B would be perfectly legal and impossible to spot
    in review. Objects still in the identity map would be from the wrong tenant.
    """


@event.listens_for(TenantSession, "after_begin")
def _bind_tenant_to_transaction(session, transaction, connection) -> None:
    ctx = current_context_or_none()
    if ctx is None:
        raise MissingTenantContext(
            "TenantSession began a transaction with no TenantContext. "
            "Data access outside a tenant scope is refused."
        )

    bound: uuid.UUID | None = session.info.get("tenant_id")
    if bound is not None and bound != ctx.tenant_id:
        raise TenantContextSwitchError(
            f"session already bound to tenant {bound}, refusing to rebind to {ctx.tenant_id}"
        )
    session.info["tenant_id"] = ctx.tenant_id

    # Transaction-scoped. A session-scoped SET would survive the connection
    # going back to the pool and serve the next request the wrong tenant.
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_GUC, "value": str(ctx.tenant_id)},
    )


TenantSessionFactory = sessionmaker(bind=None, class_=TenantSession, expire_on_commit=False)
SystemSessionFactory = sessionmaker(bind=None, class_=SystemSession, expire_on_commit=False)


@contextmanager
def tenant_session(ctx: TenantContext | None = None) -> Iterator[TenantSession]:
    """Open a tenant-scoped session.

    Pass ``ctx`` to establish the scope here, or omit it when the caller has
    already entered one (the normal path for a request).
    """
    if ctx is not None:
        with tenant_scope(ctx):
            with TenantSessionFactory(bind=app_engine()) as session:
                yield session
        return
    with TenantSessionFactory(bind=app_engine()) as session:
        yield session


@contextmanager
def system_session() -> Iterator[SystemSession]:
    """Open the cross-tenant session. Use ``SystemRepository``, not this, unless
    you are writing a migration."""
    with SystemSessionFactory(bind=system_engine()) as session:
        yield session


def commit_and_raise(session, error: Exception) -> None:
    """Persist the state change that accompanies a failure, then raise.

    Some failures are *supposed* to leave a mark: a wrong password increments a
    lockout counter, a rate-limited request records the block, a bad TOTP code
    revokes the half-open session. All of those are written inside the session
    and then abandoned, because raising out of a ``with`` block skips the commit
    on the way past — so the security mechanism silently does nothing while its
    code reads as though it works.

    Written as one named helper rather than a bare ``session.commit()`` before
    each ``raise`` so the pattern is greppable, and so a reviewer seeing it knows
    the commit is deliberate rather than misplaced.
    """
    session.commit()
    raise error
