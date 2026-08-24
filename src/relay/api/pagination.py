"""Opaque cursors for the keyset pagination TKT-5 and API-2 both need.

``TicketService.list`` pages on ``(updated_at, id)`` — keyset rather than OFFSET,
because a board sorted by ``updated_at`` changes while somebody is paging, so
OFFSET both skips and repeats rows. That pair has to travel to the client and
back, and this is the encoding.

**Why opaque.** Base64 of ``<iso8601>|<uuid>`` is not encryption and is not
pretending to be: it is a *contract boundary*. A client that reads the cursor
starts depending on the sort key, and then changing the sort — or adding a
tiebreaker — becomes a breaking change for somebody else's code (§8.6). An
opaque string keeps the ordering ours.

A malformed cursor is a **400**, not an empty page: silently returning page one
for a corrupted cursor is how a paging bug turns into an infinite loop that reads
the same rows forever.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import uuid

from relay.app.errors import ValidationFailed

MALFORMED = "分页游标无效，请从第一页重新开始。"


def encode(updated_at: dt.datetime, last_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{last_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        stamp, _, identifier = raw.partition("|")
        return dt.datetime.fromisoformat(stamp), uuid.UUID(identifier)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationFailed(MALFORMED) from exc
