"""API-4 · which URLs a webhook may be sent to (decided S-13).

**The decision**: no domain allowlist; instead, refuse private, loopback,
link-local and cloud-metadata targets outright — **and validate the resolved IP,
not only the hostname**, because otherwise DNS rebinding walks straight through
(``webhook.evil.example`` resolving to ``169.254.169.254``).

Why an allowlist was rejected: Relay is an internal tool whose webhook consumers
are other internal-ish systems that change without asking us. An allowlist would
be a list somebody has to maintain forever, and the failure mode of a stale
allowlist is a silent integration outage. Refusing private space is the property
that actually matters, because the attack this guards against is *us* becoming the
client that reaches something only we can reach.

This module is pure: it takes addresses and answers questions. Resolution is
somebody else's job (``relay.app.webhooks``) — a domain module that performs DNS
would be untestable without a network, and the rule is the part worth testing.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

#: The one address that matters most and is not private by IANA's reckoning.
#: 169.254.0.0/16 as a whole is link-local and refused anyway; it is named here
#: because "cloud metadata" is the phrase in the decision and because it is the
#: single most valuable target an SSRF can reach.
CLOUD_METADATA = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS / Azure / GCP / Alibaba
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IMDS over IPv6
    }
)

ALLOWED_SCHEMES = frozenset({"http", "https"})

SCHEME_REFUSED = "Webhook 地址必须是 http 或 https。"
HOST_REQUIRED = "Webhook 地址缺少主机名。"
PRIVATE_REFUSED = (
    "Webhook 地址不能指向内网、回环或云元数据地址。请用一个外部可达的地址。"
)
UNRESOLVABLE = "Webhook 地址的主机名无法解析。"


def scheme_and_host(url: str) -> tuple[str, str, int | None]:
    """Split a destination, raising nothing — validation is the caller's step."""
    parts = urlsplit(url.strip())
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def address_is_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether this resolved address is one we refuse to call.

    Everything here is a category rather than a list, so a new private range does
    not need a code change:

    * **private** — 10/8, 172.16/12, 192.168/16, fc00::/7, and the equivalents;
    * **loopback** — the process's own machine, which in a container means every
      sibling service on the pod network;
    * **link-local** — where cloud metadata lives;
    * **unspecified / reserved / multicast** — never a legitimate webhook target,
      and each one is a way to say "somewhere surprising";
    * IPv4-mapped IPv6 (``::ffff:10.0.0.1``), unwrapped first, because the mapped
      form otherwise reads as an ordinary global address.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address in CLOUD_METADATA:
        return True
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_reserved
        or address.is_multicast
    )


def literal_refusal(url: str) -> str | None:
    """Everything that can be judged from the string alone, or None if it passes.

    Returns a message rather than raising, so the same rule can answer "may I
    save this endpoint?" in a form and "may I deliver to it?" at send time. The
    two must not be able to disagree.
    """
    scheme, host, _ = scheme_and_host(url)
    if scheme not in ALLOWED_SCHEMES:
        return SCHEME_REFUSED
    if not host:
        return HOST_REQUIRED
    # A literal address needs no DNS, so it is judged here rather than being
    # handed to a resolver that would just hand it back.
    try:
        parsed = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    return PRIVATE_REFUSED if address_is_forbidden(parsed) else None


def resolved_refusal(addresses: list[str]) -> str | None:
    """Judge what DNS answered. Empty means it answered nothing.

    **Every** address is checked, not just the first. A hostname with one public
    and one private A record would otherwise pass here and connect to whichever
    the socket layer picked — which is the same bug as not checking at all, minus
    the reproducibility.
    """
    if not addresses:
        return UNRESOLVABLE
    for candidate in addresses:
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            return UNRESOLVABLE
        if address_is_forbidden(parsed):
            return PRIVATE_REFUSED
    return None
