"""The two writes a person can make to their own account.

Display name and password. Email and role stay out of reach: one is the
residency credential, the other is an Admin decision. The HTTP layer is in
``test_web_api.py``; this file pins the rules that would still have to hold if
the routes were rewritten.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.login import InvalidCredentials, LoginRequest, LoginUseCase
from relay.app.accounts.profile import change_password, me, update_display_name
from relay.app.accounts.sessions import SessionExpired, SessionService
from relay.app.accounts.signup import SignupRequest, SignupUseCase
from relay.app.accounts.verification import VerifyEmailUseCase
from relay.app.errors import ValidationFailed
from relay.context import tenant_scope
from relay.infra.db.models import AuditLog, User
from relay.infra.db.session import tenant_session
from relay.infra.security.passwords import verify_password
from relay.ports.mail import NullMailPort

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
NEW_PASSWORD = "N3w-Correct-Horse!"


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
def member(gateway, mail):
    signup = SignupUseCase(mail).execute(
        SignupRequest(email="dev@zerosone.test", password=PASSWORD, display_name="小雨")
    )
    token = mail.sent[-1].text_body.split("token=")[1].split()[0]
    VerifyEmailUseCase().execute(token)
    mail.sent.clear()
    return signup


def as_member(gateway, member):
    return tenant_scope(context_for(gateway.tenant_id, member.user_id))


def test_the_caller_can_read_their_own_profile(gateway, member):
    with as_member(gateway, member):
        view = me()
    assert view.user_id == member.user_id
    assert view.email == "dev@zerosone.test"
    assert view.display_name == "小雨"
    assert view.tenant_slug == "gateway"


def test_the_caller_can_rename_themselves(gateway, member):
    with as_member(gateway, member):
        view = update_display_name(" 王莉 ")
    assert view.display_name == "王莉"

    with tenant_session(context_for(gateway.tenant_id)) as session:
        stored = session.get(User, member.user_id)
        assert stored.display_name == "王莉"
        assert stored.email == "dev@zerosone.test"
        actions = list(session.scalars(select(AuditLog.action)))
    assert "account.display_name_changed" in actions


def test_a_blank_display_name_is_refused(gateway, member):
    with as_member(gateway, member):
        with pytest.raises(ValidationFailed, match="显示名不能为空"):
            update_display_name("   ")
    with as_member(gateway, member):
        assert me().display_name == "小雨"


def test_renaming_to_the_same_name_is_a_no_op(gateway, member):
    with as_member(gateway, member):
        update_display_name("小雨")
    with tenant_session(context_for(gateway.tenant_id)) as session:
        actions = list(session.scalars(select(AuditLog.action)))
    assert "account.display_name_changed" not in actions


def test_changing_password_requires_the_current_one(gateway, member):
    with as_member(gateway, member):
        with pytest.raises(ValidationFailed, match="当前密码不正确"):
            change_password("Wr0ng-Password!", NEW_PASSWORD, except_session_id=member.user_id)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        stored = session.get(User, member.user_id)
        assert verify_password(stored.password_hash, PASSWORD)


def test_the_new_password_must_actually_be_new(gateway, member):
    with as_member(gateway, member):
        with pytest.raises(ValidationFailed, match="不能与当前密码相同"):
            change_password(PASSWORD, PASSWORD, except_session_id=member.user_id)


def test_the_password_policy_applies_to_a_change(gateway, member):
    with as_member(gateway, member):
        with pytest.raises(ValidationFailed, match="至少 12 位"):
            change_password(PASSWORD, "short", except_session_id=member.user_id)


def test_changing_password_keeps_this_session_and_ends_the_others(gateway, member, mail):
    """The case ``SessionService.revoke_all_for_user`` already documented."""
    keep = LoginUseCase(mail).execute(LoginRequest(email="dev@zerosone.test", password=PASSWORD))
    drop = LoginUseCase(mail).execute(LoginRequest(email="dev@zerosone.test", password=PASSWORD))
    kept = SessionService().resolve(keep.session_token)

    with as_member(gateway, member):
        ended = change_password(PASSWORD, NEW_PASSWORD, except_session_id=kept.session_id)
    assert ended == 1

    SessionService().resolve(keep.session_token)
    with pytest.raises(SessionExpired):
        SessionService().resolve(drop.session_token)

    LoginUseCase(mail).execute(LoginRequest(email="dev@zerosone.test", password=NEW_PASSWORD))
    with pytest.raises(InvalidCredentials):
        LoginUseCase(mail).execute(LoginRequest(email="dev@zerosone.test", password=PASSWORD))

    with tenant_session(context_for(gateway.tenant_id)) as session:
        stored = session.get(User, member.user_id)
        assert verify_password(stored.password_hash, NEW_PASSWORD)
        actions = list(session.scalars(select(AuditLog.action)))
        row = session.scalars(
            select(AuditLog).where(AuditLog.action == "account.password_changed")
        ).one()
        assert row.before is None
        assert row.after is None
    assert "account.password_changed" in actions
