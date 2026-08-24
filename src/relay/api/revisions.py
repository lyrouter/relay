"""``If-Match: <rev>`` — one parser, both surfaces (§8.4, API-3, S-24).

Optimistic concurrency is the mechanism ``rev`` exists for, and it only works if
**every** mutating path demands the header. That is why this is a shared module
rather than a helper inside one router: the web layer having its own rule would be
a second concurrency policy, and under a second policy the loser of a race
silently overwrites the winner — a loss with no error, no log line, and nothing
left to reconstruct it from.

A missing header is a refusal, not a default. Defaulting it to "whatever is
current" would make the check pass by construction, which is the same as not
having it.
"""

from __future__ import annotations

from relay.app.errors import ValidationFailed

IF_MATCH_REQUIRED = "缺少 If-Match，无法安全地修改工单。请带上你看到的 rev 再试一次。"
IF_MATCH_MALFORMED = "If-Match 必须是工单的 rev（一个整数）。"


def parse_if_match(if_match: str | None) -> int:
    """Return the revision the caller believes it is updating.

    Accepts a bare integer and the quoted / weak ETag forms, because HTTP clients
    and proxies add those on their own and a caller should not have to know which
    one it got. Anything else is refused rather than guessed at.
    """
    if if_match is None:
        raise ValidationFailed(IF_MATCH_REQUIRED)
    cleaned = if_match.strip().strip('"').removeprefix("W/").strip('"')
    if not cleaned.isdigit():
        raise ValidationFailed(IF_MATCH_MALFORMED)
    return int(cleaned)
