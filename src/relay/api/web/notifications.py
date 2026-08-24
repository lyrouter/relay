"""NT-1 / NT-2 · the inbox.

F-1 made in-app the only channel in S1, and §9 insists the consequence be said
out loud: in-app notification requires people to come to the platform, so **the
unread count is the reach surface**. That is why it has its own endpoint rather
than only riding along in the session payload — a badge that updates on
navigation is the whole mechanism by which anybody learns a ticket moved.

Aggregation is why ``folded_count`` exists: five status changes on one ticket
inside the five-minute window are one inbox item saying five, not five items.
Without it the surface people depend on becomes the surface they learn to ignore.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from relay.api.dependencies import Session
from relay.app import notifications
from relay.domain.enums import NotificationType

router = APIRouter(prefix="/web/notifications", tags=["notifications"])


class InboxItemResponse(BaseModel):
    notification_id: uuid.UUID
    type: NotificationType
    target_type: str
    target_id: uuid.UUID
    payload: dict[str, Any]
    created_at: dt.datetime | None
    read_at: dt.datetime | None
    #: 1 plus however many later notifications folded into this one (NT-2).
    folded_count: int


class UnreadResponse(BaseModel):
    unread: int


class MarkedResponse(BaseModel):
    marked: int


@router.get("", response_model=list[InboxItemResponse])
def inbox(
    session: Session,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[InboxItemResponse]:
    return [
        InboxItemResponse(
            notification_id=one.notification_id,
            type=one.type,
            target_type=one.target_type,
            target_id=one.target_id,
            payload=one.payload,
            created_at=one.created_at,
            read_at=one.read_at,
            folded_count=one.folded_count,
        )
        for one in notifications.inbox(
            session.user_id, limit=limit, unread_only=unread_only
        )
    ]


@router.get("/unread-count", response_model=UnreadResponse)
def unread_count(session: Session) -> UnreadResponse:
    return UnreadResponse(unread=notifications.unread_count(session.user_id))


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(notification_id: uuid.UUID, session: Session) -> None:
    """Idempotent, and it returns 204 whether or not the row was already read.

    ``mark_read`` refuses somebody else's notification by returning False rather
    than raising, so there is nothing here to distinguish "already read" from
    "not yours" — which is the right answer for both.
    """
    notifications.mark_read(notification_id)


@router.post("/read-all", response_model=MarkedResponse)
def mark_all_read(session: Session) -> MarkedResponse:
    return MarkedResponse(marked=notifications.mark_all_read(session.user_id))
