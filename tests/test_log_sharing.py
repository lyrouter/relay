"""LOG-6 · the share-level truth table (design §6.3, S-6).

Pure rule, no database. Worth testing exhaustively here rather than through
fixtures: there are four levels times three roles times two extra facts, and the
combination that matters most — a Guest who *is* in the space — is the one a
fixture-based test is least likely to reach.
"""

from __future__ import annotations

import uuid

import pytest

from relay.app.logs.sharing import Reader, can_read
from relay.domain.enums import Role, ShareLevel

AUTHOR = uuid.uuid4()
OTHER = uuid.uuid4()

L = ShareLevel


def reader(role: Role, user_id: uuid.UUID = OTHER) -> Reader:
    return Reader(user_id=user_id, role=role)


# ------------------------------------------------------------- the author


@pytest.mark.parametrize("level", list(ShareLevel))
@pytest.mark.parametrize("role", [Role.ADMIN, Role.MEMBER, Role.GUEST])
def test_the_author_always_reads_their_own_log(level, role):
    """Ownership, checked before the level — which is why a Guest who wrote
    something can still read it back."""
    assert can_read(share_level=level, author_id=AUTHOR, reader=reader(role, AUTHOR))


# --------------------------------------------------------------- L0 · private


def test_a_private_log_is_closed_to_a_member():
    assert not can_read(share_level=L.PRIVATE, author_id=AUTHOR, reader=reader(Role.MEMBER))


def test_a_private_log_is_closed_to_a_guest():
    assert not can_read(share_level=L.PRIVATE, author_id=AUTHOR, reader=reader(Role.GUEST))


def test_an_admin_reads_a_private_log():
    """§6.3 defines L0 as "仅作者 + Admin". A real privacy decision — worth
    knowing about rather than discovering."""
    assert can_read(share_level=L.PRIVATE, author_id=AUTHOR, reader=reader(Role.ADMIN))


def test_a_grant_does_not_open_a_private_log():
    """A stale L1 grant left over from before the level was narrowed must not
    keep working — the level is checked, not the grant."""
    assert not can_read(
        share_level=L.PRIVATE,
        author_id=AUTHOR,
        reader=reader(Role.MEMBER),
        has_named_grant=True,
    )


# ----------------------------------------------------------------- L1 · named


def test_l1_needs_the_grant():
    assert not can_read(share_level=L.NAMED, author_id=AUTHOR, reader=reader(Role.MEMBER))
    assert can_read(
        share_level=L.NAMED,
        author_id=AUTHOR,
        reader=reader(Role.MEMBER),
        has_named_grant=True,
    )


def test_a_guest_reads_an_l1_grant():
    """S-6's other half: explicit grants are exactly what a Guest is for."""
    assert can_read(
        share_level=L.NAMED,
        author_id=AUTHOR,
        reader=reader(Role.GUEST),
        has_named_grant=True,
    )


def test_space_membership_does_not_substitute_for_a_grant():
    assert not can_read(
        share_level=L.NAMED,
        author_id=AUTHOR,
        reader=reader(Role.MEMBER),
        is_space_member=True,
    )


# ----------------------------------------------------------------- L2 · space


def test_l2_needs_membership():
    assert not can_read(share_level=L.SPACE, author_id=AUTHOR, reader=reader(Role.MEMBER))
    assert can_read(
        share_level=L.SPACE,
        author_id=AUTHOR,
        reader=reader(Role.MEMBER),
        is_space_member=True,
    )


def test_a_guest_in_the_space_still_cannot_read_it():
    """**S-6, the whole point of it.** "Add the contractor to the team space"
    must not hand over every log shared into that space — nobody reads the
    sentence that way, so the code must not either."""
    assert not can_read(
        share_level=L.SPACE,
        author_id=AUTHOR,
        reader=reader(Role.GUEST),
        is_space_member=True,
    )


def test_an_admin_reads_an_l2_log_without_being_in_the_space():
    assert can_read(share_level=L.SPACE, author_id=AUTHOR, reader=reader(Role.ADMIN))


# ---------------------------------------------------------------- L3 · tenant


@pytest.mark.parametrize("role", [Role.ADMIN, Role.MEMBER, Role.GUEST])
def test_l3_is_readable_by_anyone_in_the_tenant(role):
    """"全租户" means this tenant. Cross-tenant invisibility is MT's job and is
    not judged here at all (§6.3) — RLS has already excluded it."""
    assert can_read(share_level=L.TENANT, author_id=AUTHOR, reader=reader(role))


# ------------------------------------------------------------------ coverage


@pytest.mark.parametrize("level", list(ShareLevel))
def test_the_rule_is_total_over_the_levels(level):
    """No level falls through to a default. A new level added to the enum must
    fail here rather than quietly resolve to readable."""
    result = can_read(
        share_level=level,
        author_id=AUTHOR,
        reader=reader(Role.MEMBER),
        has_named_grant=False,
        is_space_member=False,
    )
    assert result is (level is ShareLevel.TENANT)
