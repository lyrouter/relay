"""MT-1 · the definitive S1 entity list, as importable models.

Nothing gets added to this package without ``tenant_id`` and an RLS policy —
``tests/test_schema_lint.py`` (MT-2) reflects ``Base.metadata`` and fails CI
otherwise, and ``tests/test_entity_registry.py`` fails if a table appears here
that is not in the written MT-1 list.

**Deliberately not created in S1** (design §4.1, §10 — table names reserved so
nobody invents a different one later):

    binding_challenge      ships with BOT
    bot_message_event      ships with BOT
    ticket_draft           ships with BOT
    bot_question_log       ships with BOT
    llm_call_record        ships with BOT (S1 makes no LLM calls)
    knowledge_unit         Phase 2 / RAG — must be same-database, same-policy
"""

from sqlalchemy import UniqueConstraint

from relay.infra.db.base import Base
from relay.infra.db.models.account import (
    EmailVerification,
    IdentityBinding,
    Invitation,
    Space,
    SpaceMember,
    User,
)
from relay.infra.db.models.api import (
    ApiIdempotencyRecord,
    ApiToken,
    WebhookDelivery,
    WebhookEndpoint,
)
from relay.infra.db.models.common import Attachment, AuditLog
from relay.infra.db.models.log import (
    Log,
    LogEditLock,
    LogShareGrant,
    LogTemplate,
    LogVersion,
)
from relay.infra.db.models.notification import Notification, NotificationDelivery
from relay.infra.db.models.session import UserSession
from relay.infra.db.models.tenant import Tenant, TenantEmailDomain
from relay.infra.db.models.throttle import Throttle
from relay.infra.db.models.ticket import (
    AiContextFieldConfig,
    Iteration,
    Label,
    Ticket,
    TicketComment,
    TicketExternalRef,
    TicketLabel,
    TicketStatusHistory,
)

# Every tenant-scoped table gets UNIQUE (id, tenant_id) so that composite
# foreign keys have something to point at (see relay.infra.db.base.tenant_fk).
#
# Applied in a loop rather than declared per model on purpose: this is the target
# side of a safety constraint, and a model that forgot to declare it would fail
# only when some *other* model tried to reference it — a confusing error, in the
# wrong file, that a reviewer would fix by dropping the composite key.
for _table in Base.metadata.tables.values():
    if "tenant_id" in _table.c and "id" in _table.c:
        _table.append_constraint(
            UniqueConstraint("id", "tenant_id", name=f"uq_{_table.name}_id_tenant_id")
        )
del _table


#: Table names reserved for later phases. Kept here so the name is settled and
#: so a future PR adding one of them is visibly completing a plan, not inventing.
RESERVED_TABLE_NAMES: frozenset[str] = frozenset(
    {
        "binding_challenge",
        "bot_message_event",
        "ticket_draft",
        "bot_question_log",
        "llm_call_record",
        "knowledge_unit",
    }
)

__all__ = [
    "AiContextFieldConfig",
    "ApiIdempotencyRecord",
    "ApiToken",
    "Attachment",
    "AuditLog",
    "Base",
    "EmailVerification",
    "IdentityBinding",
    "Invitation",
    "Iteration",
    "Label",
    "Log",
    "LogEditLock",
    "LogShareGrant",
    "LogTemplate",
    "LogVersion",
    "Notification",
    "NotificationDelivery",
    "RESERVED_TABLE_NAMES",
    "Space",
    "SpaceMember",
    "Tenant",
    "TenantEmailDomain",
    "Throttle",
    "Ticket",
    "TicketComment",
    "TicketExternalRef",
    "TicketLabel",
    "TicketStatusHistory",
    "User",
    "UserSession",
    "WebhookDelivery",
    "WebhookEndpoint",
]
