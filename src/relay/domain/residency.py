"""Tenant residency (AC-9 · S-3).

Self-service signup turns "who gets into the platform" from a human decision
into a rule. This module *is* that rule, which is why it is domain logic with no
database in sight and an exhaustive set of outcomes.

Three outcomes, and the third is the one people try to soften:

* ``AUTO_JOIN``  — the domain is allowlisted with ``auto_join`` → membership at
  ``default_role``;
* ``PENDING``    — allowlisted with ``auto_join=false`` → Admin approves;
* ``REFUSED``    — **no match refuses registration.** Not a pending pool
  (S-3). A pending pool for unknown domains is an unbounded queue of strangers
  that someone eventually clears in bulk, at which point the allowlist has
  decided nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from relay.domain.enums import Role


class ResidencyOutcome(StrEnum):
    AUTO_JOIN = "auto_join"
    PENDING = "pending"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class AllowlistedDomain:
    """The decision inputs, lifted out of the ORM so this stays testable."""

    domain: str
    default_role: Role
    auto_join: bool


@dataclass(frozen=True, slots=True)
class Residency:
    outcome: ResidencyOutcome
    role: Role | None = None
    #: Always populated for REFUSED. The cross-cutting constraint: a user-facing
    #: failure must say what to do next.
    message: str | None = None

    @property
    def admitted(self) -> bool:
        return self.outcome is not ResidencyOutcome.REFUSED


REFUSAL_MESSAGE = "该邮箱域名不在允许注册的范围内，请联系管理员获取邀请。"


def normalize_email(email: str) -> str:
    """Lowercase and strip. Applied before *every* comparison and before storage.

    Without one shared normaliser, `Someone@Example.com` and `someone@example.com`
    become two accounts, and the second one bypasses the first one's lockout.
    """
    return email.strip().lower()


def email_domain(email: str) -> str:
    """Split on the *last* ``@``, and insist there was one.

    ``rpartition`` returns the whole string as the tail when the separator is
    absent, so checking only the tail would let ``not-an-email`` through as a
    domain — and a signup for it would then be refused for the wrong reason,
    or matched against an allowlist entry someone happened to typo.
    """
    normalized = normalize_email(email)
    local, separator, domain = normalized.rpartition("@")
    if not separator or not local or not domain:
        raise ValueError(f"not an email address: {email!r}")
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError(f"not an email address: {email!r}")
    return domain


def normalize_domain(domain: str) -> str:
    """Lowercase a bare domain, or extract one from an email address.

    Signup matches on the exact stored string, so operator input has to go
    through the same rules ``email_domain`` applies — otherwise ``Example.COM``
    in the allowlist would never match ``someone@example.com``.
    """
    value = domain.strip().lower()
    if "@" in value:
        return email_domain(value)
    return email_domain(f"_@{value}")


def resolve(email: str, allowlist: AllowlistedDomain | None) -> Residency:
    """Decide what a signup with this address gets.

    ``allowlist`` is the row matching the address's domain, or None. Resolution
    is exact-match only: no subdomain matching, because ``evil.example.com``
    matching ``example.com`` is a domain-takeover away from being an open door,
    and the design fixes domain ↔ tenant as one-to-one anyway.
    """
    if allowlist is None:
        return Residency(ResidencyOutcome.REFUSED, message=REFUSAL_MESSAGE)
    if allowlist.auto_join:
        return Residency(ResidencyOutcome.AUTO_JOIN, role=allowlist.default_role)
    return Residency(ResidencyOutcome.PENDING, role=allowlist.default_role)
