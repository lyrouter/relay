"""API-1 · issuing, authenticating and revoking API tokens (design §8.2).

Four properties carry this, and each one is a decision rather than a detail:

**Hash-only storage.** The database holds a SHA-256 of the token and the caller
sees the plaintext exactly once. A dump of ``api_token`` therefore contains no
working credentials. SHA-256 rather than Argon2 because these are 256-bit random
values — there is nothing to brute-force, and a slow KDF on a path that runs for
*every API request* buys nothing (``relay.infra.security.tokens`` says the same).

**The tenant comes from the token.** Never from the request. That is why
authentication runs on the pre-tenant connection: the token is all the caller
has, so there is no tenant to scope the lookup to yet. Everything after this
point runs inside the resulting ``TenantContext``, so the isolation is the same
one the Web UI gets, from the same RLS policies.

**Authority is re-derived per request, never frozen into the credential.** A
personal token carries scopes; the *role* is read from the user row at request
time and intersected with them (``effective_capabilities``). So demoting somebody
to Guest immediately narrows every token they hold, and deactivating them stops
those tokens dead. Without that, R-2's monthly account review would be checking
the wrong artefact — the account — while the credential kept working.

**A service token is a machine, and stays visibly one.** It has no user row, its
authority is exactly its scopes, and writes it makes are recorded with
``ActorType.INTEGRATION``. Tickets it files show the machine principal as
``reporter`` (S-10), which is precisely why INT-8 must exclude these principals
from every people-metric: one alerting script would otherwise look like the
team's most productive member.

**No audit row per request.** Creation and revocation are audited; *use* is not.
An audit row per API call would bury the events that matter under traffic — the
same reasoning ``relay.infra.db.pre_tenant`` gives for not routing signup through
``SystemRepository``. What use leaves behind is ``last_used_at``, which is what
"is this token still needed?" actually asks.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app import audit
from relay.app.authz import actor_principal, require
from relay.app.errors import NotFound, PermissionDenied, ValidationFailed
from relay.context import ActorType, Origin, TenantContext
from relay.domain.enums import PrincipalType, TenantStatus, TokenScope, UserStatus
from relay.domain.permissions import Capability, TokenRequest, token_request_refusal
from relay.infra.db.models import ApiToken, Tenant, User
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.db.session import tenant_session
from relay.infra.security.tokens import generate_token, hash_token

#: §8.2. The type is in the plaintext so a leaked string can be classified
#: without a database lookup — and so that "is this a personal or a service
#: credential?" is answerable from a log line or a paste in a chat window.
PERSONAL_PREFIX = "rly_u_"
SERVICE_PREFIX = "rly_s_"

#: §8.2 · "不设过期的 token 是永久后门". 365 days by decision, with a reminder to
#: the creator 14 days out (:func:`expiring_soon`).
DEFAULT_LIFETIME = dt.timedelta(days=365)
EXPIRY_REMINDER_WINDOW = dt.timedelta(days=14)

#: How much of the random part is kept in the clear alongside the type prefix.
#: Enough to identify *which* token leaked; far too little to reconstruct it.
FINGERPRINT_CHARS = 6

TOKEN_INVALID = "API token 无效、已过期或已吊销。"
TOKEN_NOT_FOUND = "找不到该 token。"
NAME_REQUIRED = "token 需要一个名字，用来说明它是给谁用的。"
UNKNOWN_SCOPE = "未知的权限范围。"
TENANT_IN_REQUEST = "请求里不能出现 tenant_id：租户由 token 决定。"

#: A window on ``last_used_at`` writes. Every authenticated request would
#: otherwise be an UPDATE on a hot row — which turns a read-only API call into a
#: write, and makes ``api_token`` the busiest table in the database for no
#: additional information. Five minutes is far finer than any question anyone
#: asks of the column ("was this used this month?").
LAST_USED_RESOLUTION = dt.timedelta(minutes=5)


class InvalidToken(PermissionDenied):
    """Every failed authentication, whatever the cause.

    One answer for absent, malformed, revoked, expired, belonging to a
    deactivated user, or belonging to a suspended tenant. Distinguishing them
    tells a caller holding a stolen string which part of it was real, and there is
    no legitimate client that needs to know.
    """

    code = "invalid_token"


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """The one and only time the plaintext exists outside the caller's hands."""

    id: uuid.UUID
    #: Shown once. Never stored, never logged, never in an audit row.
    plaintext: str
    name: str
    principal_type: PrincipalType
    scopes: tuple[TokenScope, ...]
    expires_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class TokenView:
    id: uuid.UUID
    name: str
    principal_type: PrincipalType
    principal_user_id: uuid.UUID | None
    #: The clear part: ``rly_s_ab12cd``. What a leak report can be matched against.
    token_prefix: str
    scopes: tuple[TokenScope, ...]
    created_by: uuid.UUID | None
    created_at: dt.datetime | None
    expires_at: dt.datetime | None
    last_used_at: dt.datetime | None
    revoked_at: dt.datetime | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class AuthenticatedToken:
    """What the HTTP layer needs to serve one API request.

    Carries the tenant **slug** as well as the id because §8.3 requires every
    ticket response to include a permalink with the tenant segment (S-12), and
    resolving the slug per response would be a query per ticket.
    """

    token_id: uuid.UUID
    context: TenantContext
    tenant_slug: str
    principal_type: PrincipalType
    #: The display name a machine principal reports as ``reporter`` (S-10).
    principal_name: str
    scopes: frozenset[TokenScope]


class ApiTokenService:
    """Issuance, listing and revocation. Runs inside a ``TenantContext``.

    :meth:`authenticate` is the exception and is a ``@staticmethod`` for that
    reason: it runs *before* any context exists, because establishing one is its
    whole job.
    """

    # ----------------------------------------------------------------- issue

    def issue(
        self,
        name: str,
        principal_type: PrincipalType,
        scopes: frozenset[TokenScope],
        *,
        principal_user_id: uuid.UUID | None = None,
        lifetime: dt.timedelta | None = DEFAULT_LIFETIME,
        now: dt.datetime | None = None,
    ) -> IssuedToken:
        """Mint a token, returning the plaintext once.

        The rule about *who may mint what* is not restated here — it is
        :func:`relay.domain.permissions.token_request_refusal`, which also answers
        "may I show this button?" in the UI. A form that offers an action the
        service refuses is its own kind of bug, and one rule in one place is how
        that stays impossible.
        """
        now = now or dt.datetime.now(dt.UTC)
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValidationFailed(NAME_REQUIRED)

        with tenant_session() as session:
            actor = actor_principal(session)
            if actor.role is None:
                # A token minting another token: the credential would outlive
                # every review that authorized it, and nothing in S1 needs it.
                raise PermissionDenied(
                    "服务 token 不能再签发 token，请用管理员账号在界面里创建。"
                )
            # A personal token defaults to the caller — the only person they may
            # mint one for (see ``token_request_refusal``).
            if principal_type is PrincipalType.USER and principal_user_id is None:
                principal_user_id = actor.user_id

            refusal = token_request_refusal(
                actor.role,
                actor.user_id,
                TokenRequest(
                    principal_type=principal_type,
                    principal_user_id=principal_user_id,
                    scopes=scopes,
                ),
            )
            if refusal:
                raise PermissionDenied(refusal)

            plaintext = _mint(principal_type)
            token = ApiToken(
                tenant_id=actor.tenant_id,
                name=clean_name,
                principal_type=principal_type,
                principal_user_id=principal_user_id,
                token_prefix=_fingerprint(plaintext),
                token_hash=hash_token(plaintext),
                scopes=sorted(str(one) for one in scopes),
                created_by=actor.user_id,
                expires_at=(now + lifetime) if lifetime else None,
            )
            session.add(token)
            session.flush()
            audit.record(
                session,
                "api_token.created",
                target_type="api_token",
                target_id=token.id,
                after={
                    "name": clean_name,
                    "principal_type": str(principal_type),
                    "scopes": token.scopes,
                    # The fingerprint, never the token. An audit row is exactly
                    # the kind of long-lived record a credential must not be in.
                    "token_prefix": token.token_prefix,
                    "expires_at": token.expires_at.isoformat() if token.expires_at else None,
                },
            )
            issued = IssuedToken(
                id=token.id,
                plaintext=plaintext,
                name=clean_name,
                principal_type=principal_type,
                scopes=tuple(sorted(scopes, key=str)),
                expires_at=token.expires_at,
            )
            session.commit()
            return issued

    # ------------------------------------------------------------------ read

    def list(self, *, include_revoked: bool = False) -> list[TokenView]:
        """Tokens the caller may see: their own, plus every one in the tenant for
        an Admin.

        A Member seeing service tokens they cannot revoke would be a list of
        credentials they can do nothing about; an Admin needs the whole list,
        because "which integrations exist?" is an Admin question and R-2's review
        is an Admin activity.
        """
        with tenant_session() as session:
            actor = actor_principal(session)
            query = select(ApiToken).order_by(ApiToken.created_at.desc())
            if not actor.can(Capability.TOKEN_REVOKE_ANY):
                query = query.where(ApiToken.principal_user_id == actor.user_id)
            if not include_revoked:
                query = query.where(ApiToken.revoked_at.is_(None))
            return [_view(row) for row in session.scalars(query)]

    def expiring_soon(
        self, *, within: dt.timedelta = EXPIRY_REMINDER_WINDOW, now: dt.datetime | None = None
    ) -> list[TokenView]:
        """§8.2's 14-day reminder, as data.

        Returned rather than mailed from here: F-1 settled that S1 notifies
        in-app, and the reminder's *recipient* is the creator, which the caller
        (a route, or a scheduled job later) is better placed to reach. What
        matters is that the window is defined once, here, next to the lifetime it
        belongs to.
        """
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.CONTENT_VIEW)
            query = (
                select(ApiToken)
                .where(
                    ApiToken.revoked_at.is_(None),
                    ApiToken.expires_at.is_not(None),
                    ApiToken.expires_at <= now + within,
                    ApiToken.expires_at > now,
                )
                .order_by(ApiToken.expires_at.asc())
            )
            if not actor.can(Capability.TOKEN_REVOKE_ANY):
                query = query.where(ApiToken.created_by == actor.user_id)
            return [_view(row) for row in session.scalars(query)]

    # ---------------------------------------------------------------- revoke

    def revoke(self, token_id: uuid.UUID, *, now: dt.datetime | None = None) -> TokenView:
        """Revoke immediately — the next request on it is refused.

        Anybody may revoke their own; ``TOKEN_REVOKE_ANY`` (Admin) is required
        for somebody else's, including every service token. §5.4 puts it that way
        for a concrete reason: revoking a service token breaks an integration the
        whole team depends on, so it should not be a Member's slip.
        """
        now = now or dt.datetime.now(dt.UTC)
        with tenant_session() as session:
            actor = actor_principal(session)
            token = session.get(ApiToken, token_id)
            if token is None:
                raise NotFound(TOKEN_NOT_FOUND)
            mine = (
                token.principal_user_id is not None
                and token.principal_user_id == actor.user_id
            )
            if not mine and not actor.can(Capability.TOKEN_REVOKE_ANY):
                # NotFound rather than PermissionDenied: somebody else's token is
                # not a resource a Member should learn the existence of.
                raise NotFound(TOKEN_NOT_FOUND)

            if token.revoked_at is None:
                token.revoked_at = now
                audit.record(
                    session,
                    "api_token.revoked",
                    target_type="api_token",
                    target_id=token.id,
                    before={"name": token.name, "token_prefix": token.token_prefix},
                )
            view = _view(token)
            session.commit()
            return view

    # ---------------------------------------------------------- authenticate

    @staticmethod
    def authenticate(
        presented: str, *, now: dt.datetime | None = None
    ) -> AuthenticatedToken:
        """Resolve a bearer token into a tenant context, or refuse.

        Runs on the pre-tenant connection — see the module note — and refuses
        everything with the same message. The checks are ordered cheapest-first,
        but every branch ends in the same :class:`InvalidToken`, so the ordering
        cannot leak which one fired.

        The tenant's own status is checked here too. A suspended tenant that kept
        serving API traffic while its UI was dark would be the definition of a
        boundary nobody can see.
        """
        now = now or dt.datetime.now(dt.UTC)
        candidate = (presented or "").strip()
        if not candidate.startswith((PERSONAL_PREFIX, SERVICE_PREFIX)):
            raise InvalidToken(TOKEN_INVALID)

        with PreTenantRepository().session() as session:
            row = session.scalars(
                select(ApiToken).where(ApiToken.token_hash == hash_token(candidate))
            ).first()
            if row is None or row.revoked_at is not None:
                raise InvalidToken(TOKEN_INVALID)
            if row.expires_at is not None and row.expires_at <= now:
                raise InvalidToken(TOKEN_INVALID)

            tenant = session.get(Tenant, row.tenant_id)
            if tenant is None or tenant.status is not TenantStatus.ACTIVE:
                raise InvalidToken(TOKEN_INVALID)

            scopes = _parse_scopes(row.scopes)
            if row.principal_type is PrincipalType.SERVICE:
                actor_id, actor_type = None, ActorType.INTEGRATION
                principal_name = row.name
            else:
                user = session.get(User, row.principal_user_id)
                if user is None or user.status is not UserStatus.ACTIVE:
                    # The R-2 offboarding case: deactivating the account must
                    # kill the credentials, not wait for them to expire.
                    raise InvalidToken(TOKEN_INVALID)
                actor_id, actor_type = user.id, ActorType.USER
                principal_name = user.display_name

            if (
                row.last_used_at is None
                or now - row.last_used_at >= LAST_USED_RESOLUTION
            ):
                row.last_used_at = now
                session.flush()

            return AuthenticatedToken(
                token_id=row.id,
                context=TenantContext(
                    tenant_id=row.tenant_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    origin=Origin.API,
                    scopes=scopes,
                ),
                tenant_slug=tenant.slug,
                principal_type=row.principal_type,
                principal_name=principal_name,
                scopes=scopes,
            )


# ---------------------------------------------------------------- internals


def _mint(principal_type: PrincipalType) -> str:
    prefix = (
        SERVICE_PREFIX if principal_type is PrincipalType.SERVICE else PERSONAL_PREFIX
    )
    return f"{prefix}{generate_token()}"


def _fingerprint(plaintext: str) -> str:
    """``rly_s_`` plus the first few random characters. See ``FINGERPRINT_CHARS``."""
    prefix, _, secret = plaintext.partition("_")
    kind, _, body = secret.partition("_")
    return f"{prefix}_{kind}_{body[:FINGERPRINT_CHARS]}"


def _parse_scopes(stored: list[str] | None) -> frozenset[TokenScope]:
    """Stored as text, read back as the enum.

    An unknown string is dropped rather than raising. A scope removed from the
    product in a later version would otherwise make every old token a 500 — and
    dropping it can only *narrow* authority, which is the safe direction.
    """
    parsed = set()
    for one in stored or ():
        try:
            parsed.add(TokenScope(one))
        except ValueError:
            continue
    return frozenset(parsed)


def _view(token: ApiToken) -> TokenView:
    return TokenView(
        id=token.id,
        name=token.name,
        principal_type=token.principal_type,
        principal_user_id=token.principal_user_id,
        token_prefix=token.token_prefix,
        scopes=tuple(sorted(_parse_scopes(token.scopes), key=str)),
        created_by=token.created_by,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
    )
