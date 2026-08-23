"""IMPort — declared with a no-op implementation (§10).

BOT is deferred out of S1. The seam stays so that adding WeCom later is a new
``NotificationChannel`` plus an adapter, not a change to domain logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    external_user_id: str
    text: str


class IMPort(Protocol):
    def send(self, message: OutboundMessage) -> None:
        ...


class NoopIMPort:
    """S1 has no IM channel. This exists so wiring code can be written now and
    stay unchanged when BOT lands."""

    def send(self, message: OutboundMessage) -> None:  # noqa: ARG002 - intentional no-op
        return None
