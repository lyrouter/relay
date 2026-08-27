"""Password policy (AC-2).

Policy lives in the domain; hashing lives in infrastructure. The split matters
because the two change for different reasons — "how long must a password be" is
a product decision, "which KDF and what cost" is an operational one.

The 90-day rule is a **reminder that does not block login** (decided, S-5).
Forced rotation pushes people to `Summer2026!` → `Summer2026!!`, so the decision
is to nag and let them in.
"""

from __future__ import annotations

import datetime as dt
import re

MIN_LENGTH = 8
MAX_LENGTH = 200

#: S-5. Reminder only — `is_expired` is deliberately not a thing.
REMINDER_AFTER = dt.timedelta(days=90)

# Names are the user-facing copy, so they are written in the product's language
# rather than translated at the call site — a message assembled from English
# fragments inside a Chinese sentence is how that mix usually happens.
_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("小写字母", re.compile(r"[a-z]")),
    ("大写字母", re.compile(r"[A-Z]")),
    ("数字", re.compile(r"[0-9]")),
    ("符号", re.compile(r"[^A-Za-z0-9]")),
)

#: Three of four, not all four. Requiring every class buys very little entropy
#: and reliably produces `Password1!`.
REQUIRED_CLASSES = 3


class WeakPassword(ValueError):
    """Carries what to do next, not just what was wrong.

    The cross-cutting constraint in design §2: a user-facing failure must give
    the next step. "Password invalid" fails that; so does a bare regex.
    """


def validate(password: str, *, email: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"密码至少 {MIN_LENGTH} 位，当前 {len(password)} 位。")
    if len(password) > MAX_LENGTH:
        # Not a strength rule — an unbounded input is a hashing-cost DoS.
        raise WeakPassword(f"密码不能超过 {MAX_LENGTH} 位。")

    present = [name for name, pattern in _CLASSES if pattern.search(password)]
    if len(present) < REQUIRED_CLASSES:
        missing = [name for name, _ in _CLASSES if name not in present]
        raise WeakPassword(
            f"密码需要包含大写字母、小写字母、数字、符号中的至少 {REQUIRED_CLASSES} 类，"
            f"当前只有 {len(present)} 类。可以补上：{'、'.join(missing)}。"
        )

    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 4 and local in password.lower():
            raise WeakPassword("密码不能包含邮箱账号名 —— 这是撞库时第一个被试的组合。")


def needs_reminder(password_changed_at: dt.datetime | None, now: dt.datetime) -> bool:
    """S-5: 90 days old means remind, never refuse.

    A never-set timestamp does not trigger the reminder: it means the account
    was created before the field existed, not that the password is ancient.
    """
    if password_changed_at is None:
        return False
    return now - password_changed_at >= REMINDER_AFTER
