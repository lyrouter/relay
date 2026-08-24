"""AC-1 / AC-2 / AC-3 · signup, login, the second factor, and logout.

The only routes in the application that do not require a session, which makes
them the only routes where the throttles are the whole defence — so each one
passes :func:`relay.api.dependencies.client_ip` rather than reading a header, and
the use cases behind them consume the AC-1 per-IP and per-domain buckets.

Two shapes here are decisions, not conveniences:

**Login answers 200 with ``mfa_required: true``.** A second factor is the next
step in a flow, not a failed request. Answering 401 would trip every SPA's global
"session expired → go to login" interceptor, which is where the user already is,
and the loop that produces is a bad first impression of a security feature.

**The session cookie is set even when MFA is outstanding.** AC-3 opens a real
session row before the code is verified, and the cookie is how the TOTP request
identifies it — which keeps the half-verified token out of JavaScript, where a
"pass it back in the body" design would have to put it. Every other route refuses
that session until ``mfa_satisfied`` is true.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from relay.api.dependencies import (
    SESSION_COOKIE,
    ClientIp,
    HalfOpenSession,
    Session,
    clear_session_cookie,
    set_session_cookie,
)
from relay.api.wiring import mail_port
from relay.app.accounts import profile
from relay.app.accounts.invitations import AcceptInvitationUseCase
from relay.app.accounts.login import LoginRequest, LoginUseCase, MfaRequired
from relay.app.accounts.sessions import SessionService
from relay.app.accounts.signup import SignupRequest, SignupUseCase
from relay.app.accounts.totp import TotpService
from relay.app.accounts.verification import ResendVerificationUseCase, VerifyEmailUseCase
from relay.config import settings
from relay.domain.enums import Role

#: Addresses are ``str``, not ``EmailStr``, and that is deliberate. Two reasons,
#: the second decisive:
#:
#: * the domain layer already owns the rule (``normalize_email`` /
#:   ``email_domain``, and AC-1's residency check right behind them), and a
#:   Pydantic validator here would be a *second* email rule free to disagree with
#:   it — the same two-implementations-of-one-rule failure the share-level tests
#:   exist to catch;
#: * ``EmailStr`` refuses special-use TLDs, so ``someone@corp.internal`` — a
#:   perfectly ordinary address on an internal network, which is where Relay runs
#:   — would be rejected at the edge with a message about reserved names.
EMAIL_FIELD = Field(min_length=3, max_length=254)

router = APIRouter(prefix="/web/auth", tags=["auth"])

#: What a browser sends. Truncated to what ``UserSession`` stores, so a 40 KB
#: header cannot become a 40 KB row.
USER_AGENT_MAX = 512


class SignupPayload(BaseModel):
    email: str = EMAIL_FIELD
    password: str = Field(min_length=1)
    display_name: str = ""


class SignupResponse(BaseModel):
    outcome: str
    message: str


class TokenPayload(BaseModel):
    token: str = Field(min_length=1)


class VerifyResponse(BaseModel):
    activated: bool
    message: str


class EmailPayload(BaseModel):
    email: str = EMAIL_FIELD


class MessageResponse(BaseModel):
    message: str


class LoginPayload(BaseModel):
    email: str = EMAIL_FIELD
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    #: True means: the cookie now holds a session that can do exactly one thing,
    #: which is present a TOTP code.
    mfa_required: bool = False
    #: S-5 · surfaced as a nudge, never as a block.
    password_reminder: bool = False
    unfamiliar_network: bool = False


class TotpCodePayload(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class EnrollmentResponse(BaseModel):
    secret: str
    #: Contains the secret. Shown once, for the QR code, and never logged.
    provisioning_uri: str


class ConfirmEnrollmentPayload(BaseModel):
    secret: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=16)


class PasswordPayload(BaseModel):
    password: str = Field(min_length=1)


class AcceptInvitationPayload(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str = ""


class AcceptedResponse(BaseModel):
    tenant_id: uuid.UUID
    role: Role
    message: str


# ------------------------------------------------------------------- signup


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_202_ACCEPTED)
def signup(payload: SignupPayload, client_ip: ClientIp) -> SignupResponse:
    """202, always — the response cannot reveal whether the address is taken.

    ``SignupUseCase`` returns the same message for "mail sent" and "already
    registered" (AC-1), and a status code that differed between them would undo
    that in one line.
    """
    result = SignupUseCase(mail_port(), settings.public_base_url).execute(
        SignupRequest(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            client_ip=client_ip,
        )
    )
    return SignupResponse(outcome=str(result.outcome), message=result.message)


@router.post("/verify", response_model=VerifyResponse)
def verify_email(payload: TokenPayload) -> VerifyResponse:
    result = VerifyEmailUseCase().execute(payload.token)
    return VerifyResponse(activated=result.activated, message=result.message)


@router.post("/verification/resend", response_model=MessageResponse)
def resend_verification(payload: EmailPayload) -> MessageResponse:
    message = ResendVerificationUseCase(mail_port(), settings.public_base_url).execute(
        payload.email
    )
    return MessageResponse(message=message)


@router.post("/invitations/accept", response_model=AcceptedResponse)
def accept_invitation(payload: AcceptInvitationPayload) -> AcceptedResponse:
    """Consumes an invitation and creates the account — it does **not** log the
    invitee in. One credential per request: the password they just chose is the
    one they should use next, and a session handed out here would skip the only
    proof that they can reproduce it."""
    accepted = AcceptInvitationUseCase().execute(
        payload.token, payload.password, payload.display_name
    )
    return AcceptedResponse(
        tenant_id=accepted.tenant_id,
        role=accepted.role,
        message="账号已创建，现在可以登录了。",
    )


# -------------------------------------------------------------------- login


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginPayload, request: Request, response: Response, client_ip: ClientIp
) -> LoginResponse:
    use_case = LoginUseCase(mail_port())
    attempt = LoginRequest(
        email=payload.email,
        password=payload.password,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent", "")[:USER_AGENT_MAX],
    )
    try:
        result = use_case.execute(attempt)
    except MfaRequired as pending:
        # Not an error response: see the module note. The cookie carries the
        # half-open session so the code can be presented without the token ever
        # reaching JavaScript.
        set_session_cookie(response, pending.session_token)
        return LoginResponse(mfa_required=True)

    set_session_cookie(response, result.session_token)
    return LoginResponse(
        password_reminder=result.password_reminder,
        unfamiliar_network=result.unfamiliar_network,
    )


@router.post("/totp", response_model=LoginResponse)
def verify_totp(
    payload: TotpCodePayload,
    request: Request,
    session: HalfOpenSession,
) -> LoginResponse:
    """Satisfy the second factor for the session in the cookie.

    A wrong code **ends** the session (AC-3), so the caller is sent back to the
    login form rather than allowed to keep guessing a six-digit number against a
    token that stays valid.
    """
    TotpService().verify_login(request.cookies[SESSION_COOKIE], payload.code)
    return LoginResponse(mfa_required=False)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    """Ends the session *and* clears the cookie, in that order.

    No session dependency: logging out must work when the session is already
    unusable — expired, or awaiting a second factor — because otherwise the only
    way out of a stuck state is clearing cookies by hand.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        SessionService().logout(token)
    clear_session_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------- TOTP enrollment


@router.post("/totp/enrollment", response_model=EnrollmentResponse)
def begin_totp_enrollment(
    session: Session,
) -> EnrollmentResponse:
    """Hand out a candidate secret. **Nothing is stored yet** (AC-3): storing
    before the user has proved they can produce a code is the classic way to lock
    somebody out of their own account while enabling MFA."""
    enrollment = TotpService.begin_enrollment(profile.me().email)
    return EnrollmentResponse(
        secret=enrollment.secret, provisioning_uri=enrollment.provisioning_uri
    )


@router.post("/totp/enrollment/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_totp_enrollment(
    payload: ConfirmEnrollmentPayload,
    session: Session,
) -> Response:
    TotpService().confirm_enrollment(
        session.tenant_id, session.user_id, payload.secret, payload.code
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/totp", status_code=status.HTTP_204_NO_CONTENT)
def disable_totp(
    payload: PasswordPayload,
    session: Session,
) -> Response:
    """Removing a second factor requires the first one (AC-3) — otherwise a
    stolen session strips MFA, which is precisely the scenario it exists for."""
    TotpService().disable(session.tenant_id, session.user_id, payload.password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
