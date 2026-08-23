"""In-tenant audit writes (design §2 cross-cutting constraints).

``SystemRepository`` already audits every cross-tenant call. This is the other
half: account, permission, share-level, ticket-status, API-token and
webhook-config changes made *inside* a tenant, by a person, through the normal
path.

Two deliberate differences from the SystemRepository version:

* the row joins the **caller's transaction**, so an audit row never describes a
  change that rolled back. The reverse trade is right for a BYPASSRLS read,
  where a *failed* cross-tenant access is the more interesting event; here the
  interesting thing is the change, and a record of one that did not happen is
  noise that costs an investigator time;
* actor and origin come from the ``TenantContext`` rather than a parameter, so
  a write cannot be attributed to the wrong person by passing the wrong id — and
  a call outside a tenant scope raises instead of filing anonymously.
"""

from __future__ import annotations

import uuid

from relay.context import current_context
from relay.infra.db.models import AuditLog


def record(
    session,
    action: str,
    *,
    target_type: str,
    target_id: uuid.UUID | str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditLog:
    """Add an audit row to ``session``. The caller commits.

    ``before`` / ``after`` are for the fields that changed, not the whole row: an
    audit log that copies records grows without bound and still needs a diff
    computed to be read.
    """
    ctx = current_context()
    entry = AuditLog(
        tenant_id=ctx.tenant_id,
        actor_id=ctx.actor_id,
        actor_type=ctx.actor_type,
        origin=ctx.origin,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        before=before,
        after=after,
    )
    session.add(entry)
    return entry
