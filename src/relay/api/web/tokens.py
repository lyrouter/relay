"""API-1 · managing API tokens from the UI (§8.2, §5.4).

The public API authenticates *with* tokens; somebody has to be able to create
them, and that somebody is a person in a browser — so the management surface
belongs on ``/web``, not on ``/api/v1``. A token that could mint tokens would be a
credential that outlives every review that authorized it, which is why
``ApiTokenService.issue`` refuses a caller with no role at all.

Three things this surface makes true, none of which is cosmetic:

* **the plaintext appears in exactly one response.** There is no "show it again"
  endpoint, because the database has only a hash. The UI has to make the user copy
  it, and that is the intended friction;
* **the ``/web/session`` capability list already says who may see which button.**
  The same rule (``token_request_refusal``) answers both "may I show this form?"
  and "may I run this request?", so a form that offers a refused action cannot
  exist;
* **the 14-day expiry reminder is queryable** rather than only mailed, so the UI
  can show it. §8.2 asks for a reminder to the creator; F-1 makes in-app the only
  channel in S1, which makes this endpoint the reminder.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from relay.api.dependencies import Session
from relay.app.api_tokens import ApiTokenService, IssuedToken, TokenView
from relay.domain.enums import PrincipalType, TokenScope

router = APIRouter(prefix="/web/tokens", tags=["api tokens"])


class TokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    principal_type: PrincipalType
    principal_user_id: uuid.UUID | None
    #: ``rly_s_ab12cd`` — the clear part. What a leak report is matched against,
    #: and what the UI shows so a user can tell two tokens apart.
    token_prefix: str
    scopes: list[TokenScope]
    created_at: dt.datetime | None
    expires_at: dt.datetime | None
    last_used_at: dt.datetime | None
    revoked_at: dt.datetime | None


class IssuedResponse(BaseModel):
    token: TokenResponse
    #: **Shown once, never again.** Not stored anywhere, not in an audit row.
    plaintext: str


class CreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    #: Defaults to a personal token: it is the only kind a Member may create, and
    #: the common case even for an Admin.
    principal_type: PrincipalType = PrincipalType.USER
    scopes: list[TokenScope] = Field(min_length=1)
    #: Days. ``null`` means no expiry, which ``issue`` allows and §8.2 warns about
    #: ("不设过期的 token 是永久后门") — so the UI should not offer it lightly.
    lifetime_days: int | None = 365


def _token(view: TokenView) -> TokenResponse:
    return TokenResponse(
        id=view.id,
        name=view.name,
        principal_type=view.principal_type,
        principal_user_id=view.principal_user_id,
        token_prefix=view.token_prefix,
        scopes=list(view.scopes),
        created_at=view.created_at,
        expires_at=view.expires_at,
        last_used_at=view.last_used_at,
        revoked_at=view.revoked_at,
    )


def _issued(issued: IssuedToken, view: TokenView) -> IssuedResponse:
    return IssuedResponse(token=_token(view), plaintext=issued.plaintext)


@router.get("", response_model=list[TokenResponse])
def list_tokens(session: Session, include_revoked: bool = False) -> list[TokenResponse]:
    """Mine, or every token in the tenant for an Admin.

    A Member does not see service tokens: they could not revoke them anyway, and a
    list of credentials somebody can do nothing about is noise. R-2's review is an
    Admin activity and gets the whole list.
    """
    return [
        _token(one) for one in ApiTokenService().list(include_revoked=include_revoked)
    ]


@router.get("/expiring", response_model=list[TokenResponse])
def expiring(session: Session) -> list[TokenResponse]:
    """§8.2's 14-day warning. Empty is the normal answer."""
    return [_token(one) for one in ApiTokenService().expiring_soon()]


@router.post("", response_model=IssuedResponse, status_code=status.HTTP_201_CREATED)
def create_token(payload: CreatePayload, session: Session) -> IssuedResponse:
    """Mint a token and return the plaintext **once**.

    A personal token is bound to the caller — never to a colleague, Admin
    included: a credential that acts as somebody else attributes every audit row
    it produces to the wrong person. An Admin who needs machine access creates a
    service token, which is attributable by construction.
    """
    service = ApiTokenService()
    issued = service.issue(
        payload.name,
        payload.principal_type,
        frozenset(payload.scopes),
        lifetime=(
            dt.timedelta(days=payload.lifetime_days)
            if payload.lifetime_days is not None
            else None
        ),
    )
    # Read back so the response carries exactly what the list endpoint would show
    # (prefix, timestamps), rather than a second hand-assembled shape that could
    # drift from it.
    stored = next(one for one in service.list(include_revoked=True) if one.id == issued.id)
    return _issued(issued, stored)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(token_id: uuid.UUID, session: Session) -> Response:
    """Revoke now — the next request carrying it is refused.

    Anybody may revoke their own. Somebody else's, and every service token, needs
    ``TOKEN_REVOKE_ANY`` (Admin): revoking a service token breaks an integration
    the whole team depends on, so it should not be a Member's slip.
    """
    ApiTokenService().revoke(token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
