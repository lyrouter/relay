"""Domain enumerations.

Several of these are **frozen on release** because they appear in the public API
(design §7.2, TKT-3): renaming a ticket status is a v2-level change, not a
refactor. The wire value is the member value; the Python name may be prettier.

Clarification 2.2 (platform support tickets) replaced the engineering board
statuses with ``new / assign / working / resolved / reopen / closed`` — same
column, same enum, shared by investigation tickets and gateway-synced copies.
"""

from __future__ import annotations

from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class UserStatus(StrEnum):
    PENDING = "pending"          # registered, awaiting email verification or Admin approval
    ACTIVE = "active"
    DEACTIVATED = "deactivated"  # R-2: monthly account review, offboarding checklist


class Role(StrEnum):
    """AC-4. Three roles, checked at the service layer. No fine-grained RBAC."""

    ADMIN = "admin"
    MEMBER = "member"
    GUEST = "guest"


class SpaceRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class LogFormat(StrEnum):
    MARKDOWN = "markdown"
    PLAIN = "plain"


class ShareLevel(StrEnum):
    """LOG-6. Evaluation order is tenant filter → share level → role.

    No L4 external links in S1: it is the largest leak surface and S1 does not
    open it.
    """

    PRIVATE = "private"  # L0
    NAMED = "named"      # L1 explicit grants
    SPACE = "space"      # L2 space members
    TENANT = "tenant"    # L3 whole tenant


class TicketType(StrEnum):
    BUG = "bug"
    FEATURE = "feature"
    TASK = "task"


class SupportCategory(StrEnum):
    """Gateway support-ticket category, stored on Relay's copy (S-26)."""

    PRESALE = "presale"
    AFTERSALE = "aftersale"
    BILLING = "billing"
    TECHNICAL = "technical"
    FEEDBACK = "feedback"
    OTHER = "other"


class TicketStatus(StrEnum):
    """TKT-3 · **frozen from release** — these values ship in API responses.

    Clarification 2.2 replaced the prior engineering set
    (``todo`` / ``in_progress`` / …) with this six-value graph. ``closed`` is
    terminal; everything else can still move.
    """

    NEW = "new"
    ASSIGN = "assign"
    WORKING = "working"
    RESOLVED = "resolved"
    REOPEN = "reopen"
    CLOSED = "closed"


#: No status currently requires a written reason. Kept as an empty frozenset so
#: the UI / API can still ask when a future status needs one, without a second
#: code path.
STATUSES_REQUIRING_REASON: frozenset[TicketStatus] = frozenset()


class Priority(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class PrincipalType(StrEnum):
    """API-1. A service principal is excluded from every people-metric (INT-8)."""

    USER = "user"
    SERVICE = "service"


class TokenScope(StrEnum):
    """API-1. Four coarse scopes; deliberately not per-endpoint."""

    TICKETS_READ = "tickets:read"
    TICKETS_WRITE = "tickets:write"
    COMMENTS_WRITE = "comments:write"
    META_READ = "meta:read"


class NotificationType(StrEnum):
    """NT-1. The three events S1 notifies on (design §9).

    Stored as text in ``notification.type`` rather than a database enum: BOT and
    NT-3 add channels, not types, and a text column keeps a new type from being
    a migration. The set is deliberately closed here so that adding one is a
    decision rather than a string literal in a call site.
    """

    ASSIGNMENT = "assignment"
    MENTION = "mention"
    STATUS_CHANGE = "status_change"


class NotificationChannel(StrEnum):
    """F-1: in-app only in S1. ``email`` is declared so NT-3 is a switch, not a
    rewrite; ``wecom`` comes back with BOT."""

    INAPP = "inapp"
    EMAIL = "email"


class DeliveryState(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"  # folded into an aggregate inside the 5-minute window


class WebhookState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class WebhookDeliveryState(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"  # replayable, not discarded


class IdentityProvider(StrEnum):
    """AC-6 / AC-7. Table exists in S1; nothing writes to it."""

    WECOM = "wecom"
    GITHUB = "github"


class AiContextFieldType(StrEnum):
    STRING = "string"
    STRING_LIST = "string_list"
    NUMBER = "number"
    BOOLEAN = "boolean"
