"""AC-4 · the capability table and the token rules (design §5.4, S-6).

No database: this is the domain layer, and the point of putting the rule there
is that it can be interrogated without a request, a session, or a tenant.

The tests are written against the *decisions* rather than the table — "an Admin
cannot read a private log", not "ADMIN has 12 capabilities" — so that adding a
capability does not break them and weakening one does.
"""

from __future__ import annotations

import uuid

import pytest

from relay.domain.enums import PrincipalType, Role, ShareLevel, TokenScope
from relay.domain.permissions import (
    GUEST_REFUSAL,
    NO_SCOPES,
    PERSONAL_TOKEN_NOT_YOURS,
    SERVICE_TOKEN_HAS_USER,
    SERVICE_TOKEN_REFUSAL,
    Capability,
    TokenRequest,
    can,
    capabilities_for,
    effective_capabilities,
    role_reaches_share_level,
    share_levels_reachable_by,
    token_request_refusal,
)

ALL_ROLES = (Role.ADMIN, Role.MEMBER, Role.GUEST)


# ------------------------------------------------------------ the matrix


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_role_has_an_entry(role):
    """The table is total. A role that fell out of it would default to Guest,
    which reads as working software."""
    assert capabilities_for(role) is not None


@pytest.mark.parametrize(
    "capability",
    [
        Capability.USER_MANAGE,
        Capability.DOMAIN_ALLOWLIST_MANAGE,
        Capability.AI_CONTEXT_CONFIG,
        Capability.WEBHOOK_MANAGE,
        Capability.TOKEN_CREATE_SERVICE,
        Capability.SPACE_MANAGE,
    ],
)
def test_administrative_powers_are_admin_only(capability):
    assert can(Role.ADMIN, capability)
    assert not can(Role.MEMBER, capability)
    assert not can(Role.GUEST, capability)


@pytest.mark.parametrize(
    "capability",
    [Capability.LOG_WRITE, Capability.TICKET_WRITE, Capability.COMMENT_WRITE],
)
def test_a_member_writes_content_and_a_guest_does_not(capability):
    assert can(Role.MEMBER, capability)
    assert not can(Role.GUEST, capability)


def test_a_guest_can_only_view():
    assert capabilities_for(Role.GUEST) == frozenset({Capability.CONTENT_VIEW})


def test_a_member_may_create_a_personal_token_but_not_a_service_one():
    assert can(Role.MEMBER, Capability.TOKEN_CREATE_PERSONAL)
    assert not can(Role.MEMBER, Capability.TOKEN_CREATE_SERVICE)
    assert not can(Role.MEMBER, Capability.TOKEN_REVOKE_ANY)


# ------------------------------------------------------- share levels (S-6)


def test_a_guest_reaches_l1_and_l3_only():
    """S-6, stated as the design states it: L1 explicit grants + L3."""
    assert share_levels_reachable_by(Role.GUEST) == frozenset(
        {ShareLevel.NAMED, ShareLevel.TENANT}
    )


def test_joining_a_space_cannot_grant_a_guest_l2():
    assert not role_reaches_share_level(Role.GUEST, ShareLevel.SPACE)
    assert role_reaches_share_level(Role.MEMBER, ShareLevel.SPACE)


@pytest.mark.parametrize("role", [Role.MEMBER, Role.GUEST])
def test_only_the_author_reaches_their_own_private_log(role):
    """For everyone except Admin, L0 is an ownership question rather than a role
    one — no role value grants it."""
    assert not role_reaches_share_level(role, ShareLevel.PRIVATE)


def test_an_admin_reaches_l0():
    """Design §6.3 defines L0 as "仅作者 + Admin", which is more specific than
    §5.4's coarse "按分享级别" row and therefore wins.

    A real privacy decision rather than an oversight: administering a tenant is
    permission to read a colleague's private log here. Since L0 is the most
    restrictive level, reaching it means reaching all of them — anything else
    would make the ordering incoherent.
    """
    assert role_reaches_share_level(Role.ADMIN, ShareLevel.PRIVATE)
    assert share_levels_reachable_by(Role.ADMIN) == frozenset(ShareLevel)


def test_a_member_reaches_everything_except_l0():
    assert share_levels_reachable_by(Role.MEMBER) == frozenset(ShareLevel) - {
        ShareLevel.PRIVATE
    }


# --------------------------------------------------- effective capabilities


def test_a_session_gets_the_whole_role():
    assert effective_capabilities(Role.MEMBER, None) == capabilities_for(Role.MEMBER)


def test_a_service_token_has_only_what_its_scopes_grant():
    caps = effective_capabilities(None, frozenset({TokenScope.TICKETS_WRITE}))
    assert caps == frozenset({Capability.CONTENT_VIEW, Capability.TICKET_WRITE})
    assert Capability.USER_MANAGE not in caps
    assert Capability.WEBHOOK_MANAGE not in caps


def test_no_scope_can_reach_an_administrative_capability():
    """However a service token is created, it cannot administer the tenant."""
    every_scope = effective_capabilities(None, frozenset(TokenScope))
    assert not every_scope & {
        Capability.USER_MANAGE,
        Capability.DOMAIN_ALLOWLIST_MANAGE,
        Capability.AI_CONTEXT_CONFIG,
        Capability.WEBHOOK_MANAGE,
        Capability.SPACE_MANAGE,
        Capability.TOKEN_CREATE_SERVICE,
        Capability.TOKEN_REVOKE_ANY,
    }


def test_a_personal_token_is_the_intersection_and_a_demotion_empties_it():
    """The reason the role is re-read per request instead of frozen into the token.

    A token minted while its owner was a Member keeps working after they are
    demoted to Guest unless something intersects — and R-2's monthly review
    would then be checking a status flag while a live credential wrote tickets.
    """
    scopes = frozenset({TokenScope.TICKETS_WRITE})
    assert Capability.TICKET_WRITE in effective_capabilities(Role.MEMBER, scopes)
    assert Capability.TICKET_WRITE not in effective_capabilities(Role.GUEST, scopes)


def test_a_token_with_no_scopes_is_not_a_session():
    """An empty scope set must not collapse into "no token given"."""
    assert effective_capabilities(Role.ADMIN, frozenset()) == frozenset()
    assert effective_capabilities(Role.ADMIN, None) == capabilities_for(Role.ADMIN)


# ------------------------------------------------------------ token rules


PERSONAL_SCOPES = frozenset({TokenScope.TICKETS_READ})


def _personal(user_id: uuid.UUID) -> TokenRequest:
    return TokenRequest(PrincipalType.USER, user_id, PERSONAL_SCOPES)


def _service() -> TokenRequest:
    return TokenRequest(PrincipalType.SERVICE, None, PERSONAL_SCOPES)


def test_a_member_may_create_their_own_personal_token():
    me = uuid.uuid4()
    assert token_request_refusal(Role.MEMBER, me, _personal(me)) is None


def test_a_member_may_not_create_a_service_token():
    me = uuid.uuid4()
    assert token_request_refusal(Role.MEMBER, me, _service()) == SERVICE_TOKEN_REFUSAL


def test_an_admin_may_create_a_service_token():
    assert token_request_refusal(Role.ADMIN, uuid.uuid4(), _service()) is None


def test_a_guest_may_not_create_any_token():
    me = uuid.uuid4()
    assert token_request_refusal(Role.GUEST, me, _personal(me)) == GUEST_REFUSAL
    assert token_request_refusal(Role.GUEST, me, _service()) == GUEST_REFUSAL


def test_nobody_mints_a_personal_token_for_somebody_else():
    """Decided in AC-4, and not in §5.4: an Admin has the token power, and read
    literally that would let them issue a credential that acts as a colleague.
    Every audit row it produced would name the wrong person."""
    admin, victim = uuid.uuid4(), uuid.uuid4()
    assert token_request_refusal(Role.ADMIN, admin, _personal(victim)) == PERSONAL_TOKEN_NOT_YOURS
    member = uuid.uuid4()
    assert token_request_refusal(Role.MEMBER, member, _personal(victim)) == PERSONAL_TOKEN_NOT_YOURS


def test_a_service_token_cannot_be_bound_to_a_user():
    """A service principal that acts as a person is an unattributable actor —
    ``actor_type`` would say integration and the rows would say the user."""
    admin = uuid.uuid4()
    request = TokenRequest(PrincipalType.SERVICE, admin, PERSONAL_SCOPES)
    assert token_request_refusal(Role.ADMIN, admin, request) == SERVICE_TOKEN_HAS_USER


def test_a_token_needs_at_least_one_scope():
    me = uuid.uuid4()
    request = TokenRequest(PrincipalType.USER, me, frozenset())
    assert token_request_refusal(Role.MEMBER, me, request) == NO_SCOPES


def test_a_refusal_is_a_message_not_an_exception():
    """The same call answers "may I show this button?" and "may I run this?".

    A form that offers an action the service layer will refuse is its own kind
    of bug, so the rule has to be askable without catching something.
    """
    me = uuid.uuid4()
    assert isinstance(token_request_refusal(Role.GUEST, me, _personal(me)), str)
