"""TKT-4 · parsing ``@mentions`` out of a comment body.

The mention syntax is **the email local part**: `@lisa` reaches
`lisa@zerosone.test`. That works because AC-9 fixes domain ↔ tenant as
one-to-one, so every account in a tenant shares one domain and the local part is
already unique within it — no new handle column, nothing for a user to choose,
nothing to keep unique.

The rule this module exists to hold onto is GH sync's, arriving early:
**never @ an unrelated account.** So —

* text that is not a mention must not become one. Code spans and fenced blocks
  are stripped first, because a pasted log line or a stack trace is where stray
  ``@`` characters live;
* an ``@`` that is part of an email address is not a mention. ``ping
  bob@zerosone.test`` must not mention ``zerosone.test``;
* a handle that resolves to nobody stays plain text (the caller's job, but the
  parser makes it possible by returning handles rather than guesses).

Resolution against real accounts happens in the application layer, which is the
only place that knows who exists.
"""

from __future__ import annotations

import re

#: Runs of fenced code, then inline spans. Order matters: a fence containing
#: backticks would otherwise be half-eaten by the inline pattern.
_FENCED = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]*`")

#: ``(?<![...])`` is what keeps an email address from parsing as a mention: the
#: character before the ``@`` must not be part of a local part.
_MENTION = re.compile(r"(?<![A-Za-z0-9._%+\-])@([A-Za-z0-9][A-Za-z0-9._\-]*)")

#: Trailing punctuation belongs to the sentence, not the handle: "@lisa." and
#: "@lisa," and "@lisa's" all mean lisa.
_TRAILING = ".,-_"

#: More than this in one comment is a broadcast, and S1 has no broadcast
#: feature. The cap is where that gets noticed rather than silently delivered.
MAX_MENTIONS = 20


def strip_code(body: str) -> str:
    return _INLINE.sub(" ", _FENCED.sub(" ", body))


def parse(body: str) -> tuple[str, ...]:
    """Distinct handles, lowercased, in the order they appear.

    Order is preserved so that a capped comment reports the first offenders
    rather than an arbitrary subset, and so the behaviour is reproducible.
    """
    seen: dict[str, None] = {}
    for match in _MENTION.finditer(strip_code(body or "")):
        handle = match.group(1).rstrip(_TRAILING).lower()
        if handle:
            seen.setdefault(handle, None)
    return tuple(seen)
