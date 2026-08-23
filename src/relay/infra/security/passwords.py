"""Argon2id password hashing (AC-2).

Two properties worth knowing:

* the parameters travel inside the hash string, so raising the cost later
  rehashes each password on next login rather than needing a migration —
  ``needs_rehash`` is what makes that automatic;
* verification is deliberately slow. ``verify`` is therefore also the natural
  place to spend time on a *missing* user (see ``fake_verify``), so that
  "does this account exist?" cannot be answered with a stopwatch.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

#: A real Argon2id hash of a value nobody can supply. Used to burn the same
#: amount of time when the account does not exist, so signup enumeration and
#: login enumeration both come back flat.
_DUMMY_HASH = _hasher.hash("relay-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def fake_verify() -> None:
    """Spend a verification's worth of time against a hash that cannot match."""
    verify_password(_DUMMY_HASH, "not-the-password")


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
