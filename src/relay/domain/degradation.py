"""AC-8 · the degradation matrix (design §5.5).

Four scenarios, two of them active in S1. The other two are declared here rather
than left out, because the failure mode is not "we forgot the row" — it is BOT
or GH arriving and inventing a *different* behaviour for a case that was already
decided. ``ships_with`` records where each one lands.

The property this file exists to make checkable is the cross-cutting one:
**every active row names the next step.** A degraded path that says only "cannot
do that right now" leaves the person holding a dead end, and the one thing they
need is the sentence telling them what to do instead. :func:`unmet_next_step`
turns that from a review habit into an assertion.

Deliberately not a table of *behaviour* — the behaviour lives in the code that
degrades (``LoginUseCase`` refuses an unverified login; NT-1 will pick a
channel). This is the register those implementations are checked against.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from relay.domain.enums import NotificationChannel


class Degradation(StrEnum):
    NOTIFICATION_REACH = "notification_reach"
    UNVERIFIED_EMAIL_LOGIN = "unverified_email_login"
    CHAT_TICKET_CREATION = "chat_ticket_creation"
    GITHUB_UNMAPPED_USER = "github_unmapped_user"


@dataclass(frozen=True, slots=True)
class DegradationRow:
    scenario: Degradation
    #: Active in S1. False means "decided, and its implementation ships later".
    active: bool
    behaviour: str
    #: What the person is told to do instead. Required for an active row; None
    #: for a deferred one, whose next step is not ours to write yet.
    next_step: str | None = None
    #: The epic that implements a deferred row.
    ships_with: str | None = None


#: In S1 the *only* notification channel, which is why §5.5 says this is not a
#: fallback (F-1, design §9). ``NotificationChannel.EMAIL`` stays declared in the
#: enum so NT-3 is a switch rather than a rewrite: the sending path exists (F-5),
#: so in-app-only is a scope choice, not a capability limit — if week 6's
#: dual-track feedback is "nobody sees the notifications", turning email on is
#: ~0.5 pd and does not wait for BOT.
S1_NOTIFICATION_CHANNELS: tuple[NotificationChannel, ...] = (NotificationChannel.INAPP,)


MATRIX: dict[Degradation, DegradationRow] = {
    Degradation.NOTIFICATION_REACH: DegradationRow(
        scenario=Degradation.NOTIFICATION_REACH,
        active=True,
        behaviour="站内信是 S1 唯一的通知渠道（F-1），不是邮件失败后的降级。",
        # The consequence §9 insists on stating out loud: in-app notification
        # requires the person to come to the platform, so "my tickets" plus the
        # unread count *is* the reach surface, and telling people where to look
        # is the next step.
        next_step="在「我的工单」和未读计数中查看；这是 S1 唯一的触达面。",
    ),
    Degradation.UNVERIFIED_EMAIL_LOGIN: DegradationRow(
        scenario=Degradation.UNVERIFIED_EMAIL_LOGIN,
        active=True,
        behaviour="拒绝登录 —— 邮箱域名是自助注册唯一的归属凭据，未验证等于未证明。",
        next_step="在登录页点击「重新发送验证邮件」。",
    ),
    Degradation.CHAT_TICKET_CREATION: DegradationRow(
        scenario=Degradation.CHAT_TICKET_CREATION,
        active=False,
        behaviour="群内 @Relay 建单 / 提问，随 BOT 交付。",
        ships_with="BOT",
    ),
    Degradation.GITHUB_UNMAPPED_USER: DegradationRow(
        scenario=Degradation.GITHUB_UNMAPPED_USER,
        active=False,
        # The principle is decided even though the implementation is not, and it
        # is the half that gets broken by a well-meaning default.
        behaviour="遇未映射用户时**绝不误 @ 到无关账号**，随 GH 交付。",
        ships_with="GH",
    ),
}


def active_rows() -> tuple[DegradationRow, ...]:
    return tuple(row for row in MATRIX.values() if row.active)


def unmet_next_step() -> tuple[Degradation, ...]:
    """Active scenarios that do not tell the person what to do next.

    Should always be empty. It is a function rather than a comment so that a row
    added without a next step fails a test instead of shipping a dead end.
    """
    return tuple(row.scenario for row in active_rows() if not (row.next_step or "").strip())
