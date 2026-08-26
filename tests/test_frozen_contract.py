"""The frozen wire contract (design §8, signed off week 2).

Once the first consumer exists — the gateway WebUI feedback form — field names,
the numbering scheme and every enum value are a v2-level change. Prose cannot
enforce that. This file is the mechanical half: the literals below are copied
from §8 by hand, deliberately duplicating the enums so that *renaming a Python
member does not silently rename the wire value*. A test that derived its
expectations from the code under test would pass through any rename, which is
the same vacuous-gate problem API-5 calls out about the OpenAPI snapshot.

Changing anything here is allowed. It just cannot be done by accident: the diff
lands in review next to the design-doc change, which is the entire point.
"""

from __future__ import annotations

from relay.domain.enums import Priority, SupportCategory, TicketStatus, TicketType, TokenScope

# --------------------------------------------------------------------------
# Enum wire values. Uniform snake_case across all three (§8.3).
#
# Display names — "In Progress", "Won't Fix" — belong to the frontend. As wire
# values they would carry a space and an apostrophe into URL parameters, log
# keys and consumers' constant names.
# --------------------------------------------------------------------------

FROZEN_TICKET_TYPES = {"bug", "feature", "task"}
FROZEN_PRIORITIES = {"p0", "p1", "p2", "p3"}
FROZEN_STATUSES = {"todo", "in_progress", "in_review", "done", "blocked", "wont_fix"}
#: Additive, not a rename of TicketType. The gateway's six categories live
#: here so a Python rename cannot silently change the wire value.
FROZEN_SUPPORT_CATEGORIES = {
    "presale",
    "aftersale",
    "billing",
    "technical",
    "feedback",
    "other",
}

#: §8.2 · S-10: four coarse scopes, decided. Not per-field, not per-project.
FROZEN_SCOPES = {"tickets:read", "tickets:write", "comments:write", "meta:read"}

#: §8.2 · the prefix survives in the clear so a leaked token is identifiable.
FROZEN_TOKEN_PREFIXES = {"personal": "rly_u_", "service": "rly_s_"}

#: TKT-9 / S-12 · the tenant segment ships from day one. The first consumer
#: stores this URL, so adding the segment later is *its* breaking change.
FROZEN_PERMALINK_TEMPLATE = "https://relay.internal/{tenant_slug}/t/{number}"

#: §8.3 · reserved namespaces. Claimed now so pagination and error conventions
#: cannot diverge from /tickets when they are implemented.
RESERVED_API_NAMESPACES = {"/logs", "/search"}


def test_ticket_type_wire_values_are_frozen():
    assert {t.value for t in TicketType} == FROZEN_TICKET_TYPES


def test_priority_wire_values_are_frozen():
    assert {p.value for p in Priority} == FROZEN_PRIORITIES


def test_ticket_status_wire_values_are_frozen():
    """TKT-3: status names and semantics are frozen from here.

    They are lossy against GitHub's open/closed — that is GH's problem — and
    they appear in every API response, so a rename is v2.
    """
    assert {s.value for s in TicketStatus} == FROZEN_STATUSES


def test_support_category_wire_values_are_frozen():
    """S-26: the gateway's taxonomy, stored on Relay's copy. Additive on the
    ticket, not mixed into TicketType — renaming one is still a contract change.
    """
    assert {c.value for c in SupportCategory} == FROZEN_SUPPORT_CATEGORIES


def test_token_scopes_are_frozen():
    assert {s.value for s in TokenScope} == FROZEN_SCOPES


def test_enum_values_carry_no_characters_that_need_escaping():
    """The reason the display names were rejected as wire values.

    A consumer putting a status into `?status=` or into a metric label should
    not have to think about it.
    """
    for enum in (TicketType, Priority, TicketStatus, SupportCategory):
        for member in enum:
            assert member.value == member.value.lower()
            assert member.value.replace("_", "").isalnum(), (
                f"{enum.__name__}.{member.name} = {member.value!r} needs escaping somewhere"
            )


def test_permalink_reserves_a_tenant_segment():
    """S-12. With one tenant the UI may hide the segment; the router may not."""
    assert "{tenant_slug}" in FROZEN_PERMALINK_TEMPLATE
    rendered = FROZEN_PERMALINK_TEMPLATE.format(tenant_slug="alpha", number=331)
    assert rendered == "https://relay.internal/alpha/t/331"


def test_the_design_doc_still_says_what_this_file_says():
    """Keeps the two copies honest in the direction that actually rots.

    The literals above are hand-copied from §8 so a Python rename cannot slip
    through. The cost of that choice is drift, so this reads the document back:
    if someone edits §8.3's enum list, this fails and they update both.
    """
    from pathlib import Path

    design = (Path(__file__).resolve().parents[1] / "markdown" / "relay-s1-design.md").read_text()
    for value in sorted(
        FROZEN_STATUSES | FROZEN_TICKET_TYPES | FROZEN_PRIORITIES | FROZEN_SUPPORT_CATEGORIES
    ):
        assert f"`{value}`" in design, (
            f"{value!r} is frozen here but no longer appears in relay-s1-design.md §8.3"
        )
    assert "{tenant_slug}/t/331" in design
