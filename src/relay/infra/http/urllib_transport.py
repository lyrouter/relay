"""API-4 · the webhook transport, on the standard library.

No ``requests``/``httpx`` dependency for one POST with three headers. What this
adapter does have to get right is the thing a convenience library would do *for*
us and wrongly: **it does not follow redirects.** A consumer answering 302 with a
``Location`` of ``http://169.254.169.254/latest/meta-data/`` would otherwise walk
straight around the S-13 destination check, which was performed on the URL we
were given rather than on the one we ended up fetching.
"""

from __future__ import annotations

import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Returning None from ``redirect_request`` turns a 3xx into an HTTPError,
    which the dispatcher then treats as a failed attempt — the correct outcome,
    since we deliberately declined to follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """Implements :class:`relay.ports.webhook.WebhookTransport`."""

    def post(
        self, url: str, body: bytes, headers: dict[str, str], *, timeout: int
    ) -> int:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as answer:
                return answer.status
        except urllib.error.HTTPError as exc:
            # An HTTP error is an *answer*: it decides whether this attempt
            # retries, so it comes back as a status rather than as an exception.
            return exc.code
