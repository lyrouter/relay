"""AC-4 / AC-8 / R-2 · the account operations an Admin actually performs.

These four routes plus the invitation endpoint are what makes R-2's monthly
account review possible from inside the product. Without SSO, "deactivate in
Relay" is the *only* thing that removes access, so this is not an admin-panel
nicety — it is the mechanism the offboarding checklist depends on.

Two behaviours worth knowing before wiring a button to them:

**Deactivating ends every live session** and returns how many. That is why the
response carries a number: an Admin who has just removed somebody's access needs
to see that it took effect now, not at that person's next login.

**The last Admin cannot be deactivated or demoted.** ``bootstrap_tenant``
refuses to add a second Admin, so that door does not reopen from inside the
product — the way to get another Admin is to invite or promote one first.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from relay.api.dependencies import Session
from relay.api.wiring import mail_port
from relay.app.accounts.administration import AdminService
from relay.app.accounts.invitations import InviteUserUseCase
from relay.config import settings
from relay.domain.enums import Role

router = APIRouter(prefix="/web/admin", tags=["admin"])


class RolePayload(BaseModel):
    role: Role


class DeactivatedResponse(BaseModel):
    #: Sessions ended by this call. See the module note.
    sessions_ended: int


class InvitePayload(BaseModel):
    #: ``str``, not ``EmailStr`` — see the note in ``web/auth.py``.
    email: str = Field(min_length=3, max_length=254)
    role: Role = Role.MEMBER


class InvitedResponse(BaseModel):
    invitation_id: uuid.UUID
    message: str


@router.put("/users/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT)
def change_role(user_id: uuid.UUID, payload: RolePayload, session: Session) -> Response:
    """A demotion takes effect on that person's **next request**: the role is
    read from the stored row per call, never cached in the session."""
    AdminService().change_role(user_id, payload.role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/approval", status_code=status.HTTP_204_NO_CONTENT)
def approve(user_id: uuid.UUID, session: Session) -> Response:
    """AC-1's ``auto_join=false`` path: the domain is allowlisted but a human
    still says yes."""
    AdminService().approve_pending_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/deactivation", response_model=DeactivatedResponse)
def deactivate(user_id: uuid.UUID, session: Session) -> DeactivatedResponse:
    return DeactivatedResponse(sessions_ended=AdminService().deactivate_user(user_id))


@router.delete("/users/{user_id}/deactivation", status_code=status.HTTP_204_NO_CONTENT)
def reactivate(user_id: uuid.UUID, session: Session) -> Response:
    """Reactivation does not restore sessions — they were revoked, and a revoked
    session staying dead is the property that made deactivation trustworthy."""
    AdminService().reactivate_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/invitations", response_model=InvitedResponse, status_code=status.HTTP_201_CREATED
)
def invite(payload: InvitePayload, session: Session) -> InvitedResponse:
    """Invite one address at one role.

    Deliberately **not** checked against the domain allowlist: AC-1 tells an
    unknown domain to "contact your administrator for an invite", and a route
    that then refused the invitation for the same reason would be a dead end
    wearing a next step.
    """
    invitation_id = InviteUserUseCase(mail_port(), settings.public_base_url).execute(
        payload.email, payload.role
    )
    return InvitedResponse(
        invitation_id=invitation_id, message="邀请已发送，7 天内有效。"
    )
