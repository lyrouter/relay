"""MailPort.

F-1 decided notifications are in-app only in S1 — **a scope choice, not a
capability limit**. F-5 confirmed a transactional sending path exists, and this
port is what the two facts hang off:

* AC-1's **email verification does send** (verification is not a notification);
* NT-3 — turning email notifications on — is ~0.5 pd because the aggregation
  window and the multi-channel delivery state machine are already built. That is
  the designated escape hatch if week-6 dual-track feedback says reach is too
  low: cheaper and four weeks sooner than waiting for BOT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OutboundMail:
    to: str
    subject: str
    text_body: str
    html_body: str | None = None


class MailPort(Protocol):
    def send(self, mail: OutboundMail) -> None:
        ...


class NullMailPort:
    """Logs instead of sending. For tests and for a deployment that has not yet
    been pointed at the real relay."""

    def __init__(self) -> None:
        self.sent: list[OutboundMail] = []

    def send(self, mail: OutboundMail) -> None:
        self.sent.append(mail)

