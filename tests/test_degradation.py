"""AC-8 · the degradation matrix, and its two active rows in the real code.

The AC epic's exit condition is "every active path in AC-8 is covered by a
test". Two of these tests check the register; the rest check that the code
actually behaves the way the register says, which is the half that can drift.
"""

from __future__ import annotations

import pytest

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.login import EmailNotVerified, LoginRequest, LoginUseCase
from relay.app.accounts.signup import SignupRequest, SignupUseCase
from relay.app.accounts.verification import (
    RESEND_MESSAGE,
    ResendVerificationUseCase,
    VerifyEmailUseCase,
)
from relay.domain.degradation import (
    MATRIX,
    S1_NOTIFICATION_CHANNELS,
    Degradation,
    active_rows,
    unmet_next_step,
)
from relay.domain.enums import NotificationChannel
from relay.ports.mail import NullMailPort

from .conftest import requires_db

PASSWORD = "Corr3ct-Horse-Battery"


# ---------------------------------------------------------------- the register


def test_every_active_row_names_the_next_step():
    """The cross-cutting constraint, as an assertion rather than a review habit.

    A degraded path that says only "cannot do that right now" leaves the person
    holding a dead end.
    """
    assert unmet_next_step() == ()


def test_two_rows_are_active_in_s1():
    """§5.5: four scenarios, two live. The other two are declared so that BOT
    and GH implement the decision instead of inventing one."""
    assert {row.scenario for row in active_rows()} == {
        Degradation.NOTIFICATION_REACH,
        Degradation.UNVERIFIED_EMAIL_LOGIN,
    }


def test_every_deferred_row_says_where_it_ships():
    for row in MATRIX.values():
        if not row.active:
            assert row.ships_with, f"{row.scenario} is deferred to nowhere in particular"


def test_notifications_are_in_app_only_and_email_stays_declared():
    """F-1 with its escape hatch intact.

    In-app-only is a *scope* choice — the sending path exists (F-5) — so the
    email channel stays in the enum. That is what makes NT-3 a switch instead
    of a rewrite if week 6 says nobody sees their notifications.
    """
    assert S1_NOTIFICATION_CHANNELS == (NotificationChannel.INAPP,)
    assert NotificationChannel.EMAIL not in S1_NOTIFICATION_CHANNELS
    assert NotificationChannel.EMAIL in set(NotificationChannel)


# ------------------------------------------------- row 2, in the running code


@requires_db
@pytest.mark.db
def test_an_unverified_login_is_refused_with_a_way_forward():
    bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=PASSWORD,
        )
    )
    mail = NullMailPort()
    SignupUseCase(mail).execute(SignupRequest(email="dev@zerosone.test", password=PASSWORD))

    with pytest.raises(EmailNotVerified) as refused:
        LoginUseCase(mail).execute(
            LoginRequest(email="dev@zerosone.test", password=PASSWORD)
        )

    # Not "cannot log in": the message carries the action that fixes it, and the
    # action exists.
    assert "重新发送验证邮件" in refused.value.message
    assert ResendVerificationUseCase(mail).execute("dev@zerosone.test") == RESEND_MESSAGE


@requires_db
@pytest.mark.db
def test_the_way_forward_actually_works():
    """The next step has to lead somewhere — a resend link that produces no mail
    would satisfy the wording and none of the intent."""
    bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=PASSWORD,
        )
    )
    mail = NullMailPort()
    SignupUseCase(mail).execute(SignupRequest(email="dev@zerosone.test", password=PASSWORD))
    mail.sent.clear()

    ResendVerificationUseCase(mail).execute("dev@zerosone.test")
    token = mail.sent[-1].text_body.split("token=")[1].split()[0]
    assert VerifyEmailUseCase().execute(token).activated

    login = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=PASSWORD)
    )
    assert login.session_token
