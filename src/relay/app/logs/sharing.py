"""LOG-6 · share-level evaluation (design §6.3).

**The evaluation order is the specification**, and it is not negotiable:

    tenant filter (MT, unbypassable) → share level → role

The first step is not in this module at all — it is RLS, and that is the point.
Every query here already cannot see another tenant's rows, so "is this log in my
tenant?" is never a question this code gets to answer wrongly. §6.3 says
cross-tenant invisibility is guaranteed by MT and *not judged by LOG*.

What the levels mean, from §6.3:

===== ================= =========================================
Level Semantics         Who
===== ================= =========================================
L0    private           the author, **and Admin**
L1    named             the author, a named grant, Admin
L2    space             the author, space members whose role
                        reaches L2 (**not a Guest** — S-6), Admin
L3    whole tenant      everyone in the tenant
===== ================= =========================================

Two things a reader gets wrong here, so they are stated rather than implied:

* **An Admin reads L0.** §6.3 spells the level out as "仅作者 + Admin". Since L0
  is the most restrictive level, that necessarily means an Admin reads all of
  them; the alternative — Admin sees private logs but not space-shared ones —
  is incoherent. It is a real privacy decision, which is why an Admin reading
  somebody's private log is an ordinary administrative act with an audit trail.
* **A Guest in a space still does not reach L2** (S-6). The role is checked
  before the membership, so "add the contractor to the team space" cannot
  quietly hand over every log shared into it.

There is no L4 and no DLP. External links are the largest leak surface there is
and S1 does not open it (§6.6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from relay.domain.enums import Role, ShareLevel
from relay.domain.permissions import role_reaches_share_level


@dataclass(frozen=True, slots=True)
class Reader:
    """Who is asking, reduced to what the decision needs.

    Lifted out of the ORM for the same reason ``AllowlistedDomain`` is: the rule
    is the part worth testing exhaustively, and it should be testable without a
    database.
    """

    user_id: uuid.UUID
    role: Role


def can_read(
    *,
    share_level: ShareLevel,
    author_id: uuid.UUID,
    reader: Reader,
    has_named_grant: bool = False,
    is_space_member: bool = False,
) -> bool:
    """The single place the whole share-level question is answered.

    Callers pass the two facts that need a query (``has_named_grant``,
    ``is_space_member``); everything else is the rule. Keeping it a pure function
    is what lets the truth table be tested directly instead of through six
    fixtures.
    """
    # The author always reads their own log — an ownership test, not a role one,
    # which is why it comes before the level.
    if reader.user_id == author_id:
        return True

    # The role gate. For a Guest and L2 this is where S-6 lands, before any
    # membership lookup can talk anyone into it.
    if not role_reaches_share_level(reader.role, share_level):
        return False

    # An Admin reaches every level (§6.3's "仅作者 + Admin" at L0 implies it),
    # so no per-level condition applies to them.
    if reader.role is Role.ADMIN:
        return True

    if share_level is ShareLevel.TENANT:
        return True
    if share_level is ShareLevel.NAMED:
        return has_named_grant
    if share_level is ShareLevel.SPACE:
        return is_space_member
    # L0 for a non-author, non-Admin. Unreachable via the role gate above; kept
    # explicit so that adding a level cannot fall through to True.
    return False
