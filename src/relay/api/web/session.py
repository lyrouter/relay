"""The two reads a page load starts with: who am I, and who else is here.

``GET /web/session`` is what the SPA calls on boot. It returns the **tenant slug**
because every permalink carries one (S-12: ``/{tenant_slug}/t/331``), so the
router needs it from day one even while there is only one tenant — and it returns
**capabilities** so the UI hides what the service layer would refuse rather than
re-deriving the §5.4 matrix in TypeScript, where it would drift.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from relay.api.dependencies import Session
from relay.app import notifications
from relay.app.accounts import profile
from relay.domain.enums import Role, UserStatus

router = APIRouter(prefix="/web", tags=["session"])


class TenantSummary(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    #: Design §2 stores UTC and renders in the tenant's zone. The renderer is the
    #: frontend, so the zone travels with the session.
    timezone: str


class SessionResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: Role
    mfa_enrolled: bool
    capabilities: list[str]
    tenant: TenantSummary
    #: F-1 made in-app the only channel, which makes this number the whole reach
    #: surface. It is in the boot payload so the badge is right before the first
    #: poll rather than after it.
    unread_notifications: int


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    display_name: str
    #: ``@lisa``. What TKT-4 resolves a mention against.
    handle: str
    role: Role
    status: UserStatus


class UpdateProfilePayload(BaseModel):
    display_name: str = Field(min_length=1, max_length=profile.DISPLAY_NAME_MAX)


def _session_response() -> SessionResponse:
    me = profile.me()
    return SessionResponse(
        user_id=me.user_id,
        email=me.email,
        display_name=me.display_name,
        role=me.role,
        mfa_enrolled=me.mfa_enrolled,
        capabilities=list(me.capabilities),
        tenant=TenantSummary(
            id=me.tenant_id, slug=me.tenant_slug, name=me.tenant_name, timezone=me.timezone
        ),
        unread_notifications=notifications.unread_count(me.user_id),
    )


@router.get("/session", response_model=SessionResponse)
def current_session(session: Session) -> SessionResponse:
    return _session_response()


@router.patch("/session", response_model=SessionResponse)
def update_profile(payload: UpdateProfilePayload, session: Session) -> SessionResponse:
    """The caller's display name. Email and role are not writable here."""
    profile.update_display_name(payload.display_name)
    return _session_response()


@router.get("/users", response_model=list[MemberResponse])
def directory(session: Session, limit: int = 200) -> list[MemberResponse]:
    """The assignee picker and the mention autocomplete.

    Returns the mention handle, not the address: the handle is unavoidable
    (people type it) and the domain half carries nothing the UI needs. The public
    API's ``/meta/users`` is stricter still — id and display name only (§8.3).
    """
    return [
        MemberResponse(
            user_id=one.user_id,
            display_name=one.display_name,
            handle=one.handle,
            role=one.role,
            status=one.status,
        )
        for one in profile.members(limit=limit)
    ]
