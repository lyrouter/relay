"""AC-2 · password policy lives in the domain, not at the call sites.

Length and class counts are product decisions. These tests fail if a use case
starts inventing its own rule, or if MIN_LENGTH / REQUIRED_CLASSES drift without
the copy that users see being updated.
"""

from __future__ import annotations

import pytest

from relay.domain.passwords import WeakPassword, validate


@pytest.mark.parametrize(
    "password",
    [
        "Abcd1234",  # 8, upper + lower + digit
        "abcd123!",  # 8, lower + digit + symbol
        "ABCD12!@",  # 8, upper + digit + symbol
        "Abcd!!!!",  # 8, upper + lower + symbol
        "Ab1!xxxx",  # 8, all four
        "Corr3ct-Horse-Battery",
    ],
)
def test_a_password_with_eight_chars_and_three_classes_is_accepted(password: str) -> None:
    validate(password)


def test_seven_chars_is_refused_even_with_three_classes() -> None:
    with pytest.raises(WeakPassword, match="至少 8 位"):
        validate("Abcd123")


def test_eight_chars_with_only_two_classes_is_refused() -> None:
    with pytest.raises(WeakPassword, match="至少 3 类"):
        validate("Abcdefgh")


def test_the_local_part_of_the_email_cannot_appear_in_the_password() -> None:
    with pytest.raises(WeakPassword, match="邮箱账号名"):
        validate("Alice1!x", email="alice@zerosone.test")
