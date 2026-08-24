"""LOG-4 · the 90-day version cleanup (decided, S-8).

Two halves, and the second is what keeps the decision safe:

* versions older than **90 days** are deleted;
* **the latest version of every log is kept permanently.**

Without the second half a log nobody touched for three months would lose its
history *and* have nothing to fall back to — the cleanup would be deleting the
only copy of the current text. Cold-storage archival is deferred; this is a
delete, so the rule that stops it eating live content has to be in the query
rather than in an operator's head.

Autosave means versions accumulate fast (every distinct save is one), which is
what makes this job necessary rather than tidy.

**Who runs it (decision S-20).** The purge needs ``USER_MANAGE``, and a scheduler
has no session — which is why it could not run at all for a while.
:func:`purge_every_tenant` is the entry point a cron job calls: it runs **as the
system identity**, per tenant, under RLS. Two things it deliberately is not:
it does not borrow an Admin's account (the audit row would name someone who was
asleep), and it does not do the deleting through ``SystemRepository`` (that is
the cross-tenant BYPASSRLS path, and this is ordinary in-tenant work). The one
cross-tenant question — *which tenants exist* — is the only thing that goes
through the audited system channel.

A cleanup that ran from an application request would eventually run inside
somebody's page load, so nothing here is reachable from the HTTP layer:
``system_principal`` refuses any origin but ``SYSTEM``.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select

from relay.app import audit
from relay.app.authz import actor_principal, require
from relay.context import ActorType, Origin, TenantContext, tenant_scope
from relay.domain.permissions import Capability
from relay.infra.db.models import LogVersion
from relay.infra.db.session import tenant_session
from relay.infra.db.system_repository import SystemRepository

#: S-8.
RETENTION = dt.timedelta(days=90)


def purge_old_versions(
    *, now: dt.datetime | None = None, retention: dt.timedelta = RETENTION
) -> int:
    """Delete expired versions in the current tenant. Returns how many.

    Tenant-scoped on purpose: it runs under RLS like everything else, so a
    scheduler calls it once per tenant rather than being handed a BYPASSRLS
    connection and a WHERE clause it could get wrong.
    """
    now = now or dt.datetime.now(dt.UTC)

    with tenant_session() as session:
        # Admin-only, or the system identity a scheduler runs as (S-20). It is a
        # destructive maintenance operation, and the fact that it is normally
        # invoked by a scheduler is not a reason to let any session trigger it.
        require(actor_principal(session), Capability.USER_MANAGE)
        result = session.execute(
            delete(LogVersion).where(LogVersion.id.in_(_doomed(now - retention)))
        )
        deleted = int(result.rowcount or 0)
        if deleted:
            # Only when something was deleted: a nightly row saying "0" for
            # months is how a log stops being read. The actor comes from the
            # context, so a scheduled run files as ``system`` and a manual one
            # names the Admin who ran it (S-20).
            audit.record(
                session,
                "log.versions_purged",
                target_type="tenant",
                target_id=None,
                after={"deleted": deleted, "cutoff": (now - retention).isoformat()},
            )
        session.commit()
        return deleted


def count_old_versions(
    *, now: dt.datetime | None = None, retention: dt.timedelta = RETENTION
) -> int:
    """What :func:`purge_old_versions` would delete, deleting nothing.

    Exists so ``--dry-run`` is a real rehearsal: it runs the **same** ``_doomed``
    selection, so a rehearsal cannot report zero while the real run deletes
    thousands. A dry run built from a separate query would be a different
    program wearing the same name.
    """
    now = now or dt.datetime.now(dt.UTC)
    with tenant_session() as session:
        require(actor_principal(session), Capability.USER_MANAGE)
        return int(
            session.scalar(
                select(func.count()).select_from(_doomed(now - retention).subquery())
            )
            or 0
        )


def _doomed(cutoff: dt.datetime):
    """The versions past the window, minus the one every log must keep.

    The kept version is computed as ``MAX(version_no)`` rather than read from
    ``log.current_version``: if the two ever disagreed, trusting the counter
    would delete the row the log actually points at.
    """
    latest = (
        select(LogVersion.log_id, func.max(LogVersion.version_no).label("keep"))
        .group_by(LogVersion.log_id)
        .subquery()
    )
    return select(LogVersion.id).where(
        LogVersion.created_at < cutoff,
        LogVersion.id.not_in(
            select(LogVersion.id).join(
                latest,
                (LogVersion.log_id == latest.c.log_id)
                & (LogVersion.version_no == latest.c.keep),
            )
        ),
    )


def system_context(tenant_id) -> TenantContext:
    """The context a scheduled run establishes for one tenant (S-20).

    Written as a named function rather than inline so that every scheduled job
    gets the same three values, and so ``grep system_context`` finds every
    place something runs without a person behind it. ``actor_id`` is None on
    purpose: ``system_principal`` refuses a system run that carries a user id,
    because the point of the identity is to *not* be somebody.
    """
    return TenantContext(
        tenant_id=tenant_id,
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        origin=Origin.SYSTEM,
    )


def purge_every_tenant(
    *,
    now: dt.datetime | None = None,
    retention: dt.timedelta = RETENTION,
    dry_run: bool = False,
) -> dict[str, int]:
    """What cron calls. Returns ``{tenant_slug: versions deleted (or doomed)}``.

    One tenant per transaction, so a tenant whose purge fails does not take the
    others down with it — and the count is reported per slug rather than as one
    partial number nobody can interpret.

    The tenant *list* is the only cross-tenant read here, and it goes through
    ``SystemRepository`` with a written reason, so the audit trail says why a
    BYPASSRLS connection was opened. Everything after that is per-tenant work
    under RLS, exactly as a request would do it.
    """
    tenants = SystemRepository().list_tenants("scheduled 90-day log version purge (S-8)")
    work = count_old_versions if dry_run else purge_old_versions
    counted: dict[str, int] = {}
    for tenant in tenants:
        with tenant_scope(system_context(tenant.id)):
            counted[tenant.slug] = work(now=now, retention=retention)
    return counted
