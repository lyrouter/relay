"""AC-5 · team spaces, and S-6 — the rule that a Guest gains nothing by joining.

The single most important test in this file is
``test_a_guest_in_the_space_still_does_not_get_l2``. Everything else here is
ordinary CRUD with permission checks; that one is the decision.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from relay.app.accounts.administration import AdminService
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.signup import SignupRequest, SignupUseCase
from relay.app.accounts.spaces import LAST_OWNER, NOT_SPACE_ADMIN, SpaceService
from relay.app.accounts.verification import VerifyEmailUseCase
from relay.app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from relay.context import tenant_scope
from relay.domain.enums import Role, SpaceRole
from relay.infra.db.models import AuditLog, Space, SpaceMember
from relay.infra.db.session import tenant_session
from relay.ports.mail import NullMailPort

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"


@pytest.fixture
def mail():
    return NullMailPort()


@pytest.fixture
def gateway():
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=PASSWORD,
        )
    )


@pytest.fixture
def signup_user(mail):
    def make(email: str) -> uuid.UUID:
        result = SignupUseCase(mail).execute(SignupRequest(email=email, password=PASSWORD))
        token = mail.sent[-1].text_body.split("token=")[1].split()[0]
        VerifyEmailUseCase().execute(token)
        mail.sent.clear()
        return result.user_id

    return make


@pytest.fixture
def member(signup_user):
    return signup_user("dev@zerosone.test")


@pytest.fixture
def guest(gateway, signup_user):
    user_id = signup_user("contractor@zerosone.test")
    with as_admin(gateway):
        AdminService().change_role(user_id, Role.GUEST)
    return user_id


def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


def as_user(gateway, user_id):
    return tenant_scope(context_for(gateway.tenant_id, user_id))


def members_of(tenant_id: uuid.UUID, space_id: uuid.UUID) -> dict[uuid.UUID, SpaceRole]:
    with tenant_session(context_for(tenant_id)) as session:
        rows = session.scalars(select(SpaceMember).where(SpaceMember.space_id == space_id)).all()
        return {row.user_id: row.space_role for row in rows}


# ------------------------------------------------------------------ creation


def test_an_admin_creates_a_space_and_owns_it(gateway):
    with as_admin(gateway):
        space_id = SpaceService().create("平台组", "网关与 Relay")

    assert members_of(gateway.tenant_id, space_id) == {gateway.admin_user_id: SpaceRole.OWNER}
    with tenant_session(context_for(gateway.tenant_id)) as session:
        actions = list(session.scalars(select(AuditLog.action)))
    assert "space.created" in actions


def test_a_member_cannot_create_a_space(gateway, member):
    """Creating a space and filling it with people is granting L2 read access,
    which sits with the other access-granting powers."""
    with as_user(gateway, member):
        with pytest.raises(PermissionDenied):
            SpaceService().create("影子组")


def test_a_duplicate_name_is_refused(gateway):
    with as_admin(gateway):
        service = SpaceService()
        service.create("平台组")
        with pytest.raises(Conflict):
            service.create("  平台组  ")


def test_a_blank_name_is_refused(gateway):
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            SpaceService().create("   ")


def test_spaces_do_not_nest(gateway):
    """AC-5 is "single level, no nesting", and this pins it in the schema.

    A tree would turn "who can read this" into a recursive question, which is
    the kind nobody checks by looking. The decision is cheap to keep and
    expensive to undo, so the absence of a parent column is asserted rather
    than described.
    """
    columns = set(Space.__table__.c.keys())
    assert not {"parent_id", "parent_space_id", "path", "depth"} & columns
    assert not [fk for fk in Space.__table__.foreign_keys if fk.column.table is Space.__table__]


# -------------------------------------------------------------- membership


def test_an_admin_adds_a_member(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)

    assert members_of(gateway.tenant_id, space_id)[member] is SpaceRole.MEMBER


def test_a_space_owner_who_is_not_an_admin_manages_its_membership(gateway, member, signup_user):
    """The per-object half of AC-4: running a space is not administering a tenant."""
    other = signup_user("second@zerosone.test")
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member, SpaceRole.OWNER)

    with as_user(gateway, member):
        SpaceService().add_member(space_id, other)

    assert other in members_of(gateway.tenant_id, space_id)


def test_a_plain_space_member_cannot_change_membership(gateway, member, signup_user):
    other = signup_user("second@zerosone.test")
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)

    with as_user(gateway, member):
        with pytest.raises(PermissionDenied) as refused:
            SpaceService().add_member(space_id, other)
    assert refused.value.message == NOT_SPACE_ADMIN


def test_adding_the_same_member_twice_is_a_no_op(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)
        service.add_member(space_id, member)

    assert len(members_of(gateway.tenant_id, space_id)) == 2


def test_adding_an_existing_member_with_a_new_role_promotes_them(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)
        service.add_member(space_id, member, SpaceRole.OWNER)

    assert members_of(gateway.tenant_id, space_id)[member] is SpaceRole.OWNER


def test_a_guest_may_be_added_to_a_space(gateway, guest):
    """S-6 governs what membership *grants*, not who may hold it. Refusing the
    membership would make "show the contractor the two things they need"
    impossible to express."""
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, guest)

    assert guest in members_of(gateway.tenant_id, space_id)


def test_a_member_is_removed(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)
        service.remove_member(space_id, member)

    assert member not in members_of(gateway.tenant_id, space_id)


def test_the_last_owner_cannot_be_removed(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)
        with pytest.raises(Conflict) as refused:
            service.remove_member(space_id, gateway.admin_user_id)
    assert refused.value.message == LAST_OWNER


def test_an_owner_can_be_removed_once_there_is_another(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member, SpaceRole.OWNER)
        service.remove_member(space_id, gateway.admin_user_id)

    assert members_of(gateway.tenant_id, space_id) == {member: SpaceRole.OWNER}


def test_the_last_owner_cannot_be_demoted_either(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        with pytest.raises(Conflict):
            service.add_member(space_id, gateway.admin_user_id, SpaceRole.MEMBER)


def test_removing_a_non_member_is_not_found(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        with pytest.raises(NotFound):
            service.remove_member(space_id, member)


def test_a_user_from_another_tenant_cannot_be_added(gateway):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="其他团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        with pytest.raises(NotFound):
            service.add_member(space_id, other.admin_user_id)


def test_a_deactivated_user_cannot_be_added(gateway, member):
    with as_admin(gateway):
        AdminService().deactivate_user(member)
        service = SpaceService()
        space_id = service.create("平台组")
        with pytest.raises(NotFound):
            service.add_member(space_id, member)


def test_another_tenants_space_is_not_found(gateway):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="其他团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        elsewhere = SpaceService().create("他们的组")

    with as_admin(gateway):
        with pytest.raises(NotFound):
            SpaceService().add_member(elsewhere, gateway.admin_user_id)


# ------------------------------------------------------------- L2 (S-6)


def test_a_member_of_the_space_reaches_l2(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)

    with as_user(gateway, member):
        assert SpaceService().grants_space_read(space_id, member, Role.MEMBER)


def test_a_member_outside_the_space_does_not(gateway, member):
    with as_admin(gateway):
        space_id = SpaceService().create("平台组")

    with as_user(gateway, member):
        assert not SpaceService().grants_space_read(space_id, member, Role.MEMBER)


def test_a_guest_in_the_space_still_does_not_get_l2(gateway, guest):
    """S-6, and the reason ``grants_space_read`` checks the role first.

    "Add the contractor to the team space" must not hand over every log shared
    into it — nobody reads the sentence that way, so the code must not either.
    """
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, guest)

    assert guest in members_of(gateway.tenant_id, space_id)
    with as_user(gateway, guest):
        assert not SpaceService().grants_space_read(space_id, guest, Role.GUEST)


def test_space_membership_is_what_this_predicate_answers(gateway, member):
    """``grants_space_read`` answers the *membership* half of L2, for any role.

    An Admin outside the space gets False here — and still reads the log,
    because §6.3 gives Admin every level. That resolution lives in
    ``relay.app.logs.sharing.can_read``, which is the only place the whole L2
    question should be asked.
    """
    with as_admin(gateway):
        service = SpaceService()
        own = service.create("平台组")
        theirs = service.create("另一个组")
        service.add_member(theirs, member, SpaceRole.OWNER)
        service.remove_member(theirs, gateway.admin_user_id)

        assert service.grants_space_read(own, gateway.admin_user_id, Role.ADMIN)
        assert not service.grants_space_read(theirs, gateway.admin_user_id, Role.ADMIN)


def test_space_ids_for_lists_only_that_users_spaces(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        joined = service.create("平台组")
        service.create("另一个组")
        service.add_member(joined, member)

    with as_user(gateway, member):
        assert SpaceService().space_ids_for(member) == frozenset({joined})


def test_member_ids_lists_the_space(gateway, member):
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("平台组")
        service.add_member(space_id, member)
        assert set(service.member_ids(space_id)) == {gateway.admin_user_id, member}
