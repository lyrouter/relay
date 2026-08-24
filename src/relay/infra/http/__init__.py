"""Outbound HTTP adapters. Today: the webhook transport (API-4)."""

from relay.infra.http.urllib_transport import UrllibTransport

__all__ = ["UrllibTransport"]
