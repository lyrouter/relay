"""AC-4 / AC-8 / R-2 · the account operations an Admin actually performs.

``GET /web/admin/users`` plus the four mutations and the invitation endpoint
are what makes R-2's monthly account review possible from inside the product.
Without SSO, "deactivate in Relay" is the *only* thing that removes access, so
this is not an admin-panel nicety — it is the mechanism the offboarding
checklist depends on.

Two behaviours worth knowing before wiring a button to them:

**Deactivating ends every live session** and returns how many. That is why the
response carries a number: an Admin who has just removed somebody's access needs
to see that it took effect now, not at that person's next login.

**The last Admin cannot be deactivated or demoted.** ``bootstrap_tenant``
refuses to add a second Admin, so that door does not reopen from inside the
product — the way to get another Admin is to invite or promote one first.

INT-8's acceptance dashboard is also here, at the end. It sits with the admin
routes because it is the acceptance review's screen, not because it needs admin
rights — it is aggregate counts and requires only ``CONTENT_VIEW``.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from relay.api.dependencies import Session
from relay.api.wiring import mail_port
from relay.app.accounts.administration import AdminService
from relay.app.accounts.invitations import InviteUserUseCase
from relay.app.metrics import AcceptanceDashboard
from relay.config import settings
from relay.domain.enums import Role, UserStatus

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


class AdminUserResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    handle: str
    role: Role
    status: UserStatus
    email_verified_at: dt.datetime | None
    created_at: dt.datetime
    last_login_at: dt.datetime | None


@router.get("/users", response_model=list[AdminUserResponse])
def list_users(session: Session, limit: int = 200) -> list[AdminUserResponse]:
    """R-2's review list, and the screen that lets an Admin approve a signup.

    ``GET /web/users`` stays the assignee picker: no addresses, no leavers.
    Emails live here because they are the residency credential, and a monthly
    account review that cannot see who registered is not a review.
    """
    return [
        AdminUserResponse(
            user_id=one.user_id,
            email=one.email,
            display_name=one.display_name,
            handle=one.handle,
            role=one.role,
            status=one.status,
            email_verified_at=one.email_verified_at,
            created_at=one.created_at,
            last_login_at=one.last_login_at,
        )
        for one in AdminService().list_users(limit=limit)
    ]


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


class WeeklyCreatorsResponse(BaseModel):
    """INT-8's headline number, **carrying its own denominator**.

    Both halves ship together on purpose: a share quoted without the population
    it is a share of is the number that gets argued about in the acceptance
    review, which is exactly what pinning the definitions was meant to prevent.
    """

    week_start: dt.datetime
    activated_accounts: int
    active_creators: int
    share: float


class DashboardResponse(BaseModel):
    tenant_slug: str
    generated_at: dt.datetime
    creators: WeeklyCreatorsResponse
    logs_this_week: int
    tickets_this_week: int
    #: LOG-9 · S-16: checked **and** body ≥ 300 characters, by the product's own
    #: rule rather than a second copy of it. P-4 still spot-checks ten by hand —
    #: a count is not a quality judgement.
    knowledge_candidates: int
    tickets_by_status: dict[str, int]
    webhook_delivered: int
    webhook_pending: int
    webhook_dead_letter: int


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(session: Session) -> DashboardResponse:
    """INT-8 · the minimal acceptance dashboard.

    Read-only and aggregate: counts, never titles, so it needs no more than
    ``CONTENT_VIEW`` and shows a Guest nothing they could not already see. The
    denominators live in ``relay.app.metrics`` — see its note on why they are code
    and not a conversation.
    """
    snapshot = AcceptanceDashboard().snapshot()
    return DashboardResponse(
        tenant_slug=snapshot.tenant_slug,
        generated_at=snapshot.generated_at,
        creators=WeeklyCreatorsResponse(
            week_start=snapshot.creators.week_start,
            activated_accounts=snapshot.creators.activated_accounts,
            active_creators=snapshot.creators.active_creators,
            share=round(snapshot.creators.share, 4),
        ),
        logs_this_week=snapshot.logs_this_week,
        tickets_this_week=snapshot.tickets_this_week,
        knowledge_candidates=snapshot.knowledge_candidates,
        tickets_by_status=snapshot.tickets_by_status,
        webhook_delivered=snapshot.webhook_delivered,
        webhook_pending=snapshot.webhook_pending,
        webhook_dead_letter=snapshot.webhook_dead_letter,
    )
