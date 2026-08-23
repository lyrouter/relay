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
what makes this job necessary rather than tidy. **Nothing schedules it here** —
wiring it to a scheduler is ops work (INT), and a cleanup that runs from an
application request would eventually run inside somebody's page load.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select

from relay.app.authz import actor_principal, require
from relay.domain.permissions import Capability
from relay.infra.db.models import LogVersion
from relay.infra.db.session import tenant_session

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
    cutoff = now - retention

    with tenant_session() as session:
        # Admin-only. It is a destructive maintenance operation, and the fact
        # that it is normally invoked by a scheduler is not a reason to let any
        # session trigger it.
        require(actor_principal(session), Capability.USER_MANAGE)

        # The version to keep for each log, computed rather than read from
        # log.current_version: if the two ever disagreed, trusting the counter
        # would delete the row the log actually points at.
        latest = (
            select(LogVersion.log_id, func.max(LogVersion.version_no).label("keep"))
            .group_by(LogVersion.log_id)
            .subquery()
        )
        doomed = select(LogVersion.id).where(
            LogVersion.created_at < cutoff,
            LogVersion.id.not_in(
                select(LogVersion.id).join(
                    latest,
                    (LogVersion.log_id == latest.c.log_id)
                    & (LogVersion.version_no == latest.c.keep),
                )
            ),
        )
        result = session.execute(
            delete(LogVersion).where(LogVersion.id.in_(doomed))
        )
        session.commit()
        return int(result.rowcount or 0)
