"""Network familiarity (AC-2, unfamiliar-location alert).

The design asks for an "unfamiliar location" alert. Real geolocation needs a
MaxMind-style database — a new external dependency, a licence, and a refresh
job, none of which are in AC-2's 1.5 pd. So the signal here is **unfamiliar
network**, computed from data we already have.

That is a weaker signal than geography and it is worth being honest about which
way it is weaker: it will not notice an attacker on the same office network, and
it *will* fire on a VPN change. Both failure modes are visible to the person
receiving the alert, which is the property that matters — a false positive costs
them a glance, and the alternative is no alert at all.

Aggregating to a prefix rather than comparing exact addresses is what keeps it
from firing on every DHCP renewal.
"""

from __future__ import annotations

import ipaddress

#: /24 for IPv4, /48 for IPv6 — roughly "the same site" in both families.
IPV4_PREFIX = 24
IPV6_PREFIX = 48


def network_key(ip_address: str | None) -> str | None:
    """Reduce an address to the prefix used for comparison.

    Returns None for anything unparseable, and callers treat None as "cannot
    judge" rather than as "unfamiliar" — alerting because a proxy sent a
    malformed header would train people to ignore the alert.
    """
    if not ip_address:
        return None
    try:
        parsed = ipaddress.ip_address(ip_address.strip())
    except ValueError:
        return None
    prefix = IPV4_PREFIX if parsed.version == 4 else IPV6_PREFIX
    return str(ipaddress.ip_network(f"{parsed}/{prefix}", strict=False))


def is_unfamiliar(candidate: str | None, known: set[str | None]) -> bool:
    key = network_key(candidate)
    if key is None:
        return False
    known_keys = {k for k in known if k is not None}
    # A first-ever login is not "unfamiliar" — there is nothing to compare it
    # to, and alerting on it would make the very first thing a new user sees a
    # security warning about themselves.
    if not known_keys:
        return False
    return key not in known_keys
