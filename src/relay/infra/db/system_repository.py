"""The one declared cross-tenant path (MT-3).

Design §4.2: "the only cross-tenant path is an explicitly declared
``SystemRepository``, available to migrations and platform operations only, and
every call is audited."

Two things make that sentence true rather than aspirational:

* it runs on its own BYPASSRLS connection, so nobody has to "temporarily turn
  the policy off" — a maneuver that is one forgotten ``ENABLE`` away from a
  permanently open database;
* every call writes an ``audit_log`` row before the work runs, so an unexplained
  cross-tenant read leaves evidence even if the operation then fails.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from sqlalchemy import select

from relay.context import ActorType, Origin
from relay.infra.db.models import AuditLog, Tenant
from relay.infra.db.session import SystemSession, system_session

T = TypeVar("T")


class SystemRepository:
    """Platform operations that legitimately span tenants.

    Every public method takes ``reason`` — free text, required, and stored. If a
    caller cannot articulate why it needs to cross the tenant boundary, that is
    the review signal.
    """

    def __init__(self, actor_id: uuid.UUID | None = None) -> None:
        self._actor_id = actor_id

    @contextmanager
    def _audited(
        self, action: str, reason: str, tenant_id: uuid.UUID | None
    ) -> Iterator[SystemSession]:
        """Record the intent, commit it, *then* do the work.

        Two transactions, on purpose. If the audit row shared a transaction with
        the operation, a cross-tenant read that then failed would leave no trace
        — and an unexplained cross-tenant read that errored is more interesting
        than one that succeeded, not less.
        """
        if not reason.strip():
            raise ValueError("SystemRepository requires a written reason for every call")

        with system_session() as audit:
            audit.add(
                AuditLog(
                    # A cross-tenant call still needs somewhere to file the
                    # record. Tenant-specific work files under that tenant;
                    # genuinely global work files under the platform tenant.
                    tenant_id=tenant_id or self._platform_tenant_id(audit),
                    actor_id=self._actor_id,
                    actor_type=ActorType.SYSTEM,
                    origin=Origin.SYSTEM,
                    action=f"system_repository.{action}",
                    target_type="tenant",
                    target_id=str(tenant_id) if tenant_id else None,
                    after={"reason": reason},
                )
            )
            audit.commit()

        with system_session() as session:
            yield session
            session.commit()

    @staticmethod
    def _platform_tenant_id(session: SystemSession) -> uuid.UUID:
        """The lowest-slug tenant, used as the filing cabinet for global calls.

        Deliberately not a magic NULL: ``audit_log.tenant_id`` is NOT NULL so
        that an audit row can never become invisible to every policy at once.
        """
        tenant_id = session.scalars(select(Tenant.id).order_by(Tenant.slug).limit(1)).first()
        if tenant_id is None:
            raise RuntimeError("no tenant exists yet; run the AC-9 bootstrap first")
        return tenant_id

    def list_tenants(self, reason: str) -> list[Tenant]:
        with self._audited("list_tenants", reason, None) as session:
            return list(session.scalars(select(Tenant).order_by(Tenant.slug)))

    def run_creating_tenant(
        self,
        action: str,
        reason: str,
        fn: Callable[[SystemSession], T],
        tenant_id_of: Callable[[T], uuid.UUID],
    ) -> T:
        """The genesis operation: work that *creates* the tenant it files under.

        Named this narrowly on purpose — it is not a general escape hatch, and a
        call site that is not creating a tenant should not compile past review.

        It exists because AC-9's bootstrap hits a real ordering problem: the
        audit row needs a tenant to belong to (``audit_log.tenant_id`` is NOT
        NULL, so that no audit row can end up invisible to every policy), and
        the first tenant does not exist yet.

        Unlike :meth:`run`, the audit row here is written **inside** the
        operation's transaction rather than committed ahead of it. That is the
        right trade for this one case: an audit row saying "created tenant X"
        is meaningless if creating tenant X rolled back. The reasoning that
        makes :meth:`run` commit first — a *failed* cross-tenant read is more
        interesting than a successful one — does not apply to creation.
        """
        if not reason.strip():
            raise ValueError("SystemRepository requires a written reason for every call")
        with system_session() as session:
            result = fn(session)
            session.add(
                AuditLog(
                    tenant_id=tenant_id_of(result),
                    actor_id=self._actor_id,
                    actor_type=ActorType.SYSTEM,
                    origin=Origin.SYSTEM,
                    action=f"system_repository.{action}",
                    target_type="tenant",
                    target_id=str(tenant_id_of(result)),
                    after={"reason": reason},
                )
            )
            session.commit()
            return result

    def run(
        self,
        action: str,
        reason: str,
        fn: Callable[[SystemSession], T],
        tenant_id: uuid.UUID | None = None,
    ) -> T:
        """Escape hatch for one-off platform work, still audited.

        Prefer a named method. This exists so that "I need something the
        repository does not have" never becomes "I will open my own BYPASSRLS
        connection", which would be unaudited.
        """
        with self._audited(action, reason, tenant_id) as session:
            return fn(session)
