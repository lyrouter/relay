"""WebhookTransport (API-4) — the one thing that actually touches the network.

A port rather than a direct ``urlopen`` inside the dispatcher, for two reasons
that both matter:

* the retry ladder, the dead letter and the signature can be tested without a
  server. A retry schedule verified only against a real endpoint is a retry
  schedule nobody verifies;
* it is a **single choke point**. The S-13 destination check runs immediately
  before the call; if sending were inline in three places, the check would have to
  be remembered in three places.
"""

from __future__ import annotations

from typing import Protocol


class WebhookTransport(Protocol):
    def post(
        self, url: str, body: bytes, headers: dict[str, str], *, timeout: int
    ) -> int:
        """POST ``body`` and return the HTTP status.

        Implementations **must not follow redirects**: a 302 into private space
        would walk around the destination check performed on the original URL.
        A transport-level failure raises; an HTTP error status is returned, because
        the dispatcher decides retries from the status.
        """
        ...
