"""AC-4 · the role capability table (design §5.4).

Three roles, **no fine-grained RBAC**. The design says that twice, so the shape
here is a fixed table rather than an extensible grant system: adding a
capability means editing this file in a review, which is the point.

Two properties are worth stating because they are the ones a reader assumes
wrongly:

* **An Admin reads every share level, including L0.** Design §6.3 spells the
  level out as "仅作者 + Admin". It is worth knowing that this is a real
  privacy decision and not an oversight: administering a tenant *is* permission
  to read a colleague's private log here, which is why the L0 read path is
  audited like any other administrative act.
* **A capability the table does not grant is denied.** There is no wildcard and
  no default-allow branch; :func:`capabilities_for` is total over ``Role``.

The token half of AC-4 lives here too (:func:`token_request_refusal`,
:func:`effective_capabilities`) rather than waiting for API-1. API-1 builds
issuance — prefixes, hashing, expiry — and calls these. Keeping the rule in the
domain layer is what lets it be tested without an HTTP request, and stops
"who may mint a token" from being decided inside a router.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from relay.domain.enums import PrincipalType, Role, ShareLevel, TokenScope


class Capability(StrEnum):
    """One member per row of the §5.4 matrix, and no more.

    Deliberately coarse. ``TICKET_WRITE`` covers create, edit and transition
    because the design groups them (工单创建 / 编辑 / 流转) — splitting them
    would be the fine-grained RBAC the design rules out, arrived at by
    accident.
    """

    USER_MANAGE = "user_manage"                    # 用户管理 / 审批 / 角色变更
    DOMAIN_ALLOWLIST_MANAGE = "domain_allowlist_manage"  # 域名白名单配置 (AC-9)
    AI_CONTEXT_CONFIG = "ai_context_config"        # AI 上下文字段显隐配置 (TKT-2)
    SPACE_MANAGE = "space_manage"                  # AC-5, see the note below
    LOG_WRITE = "log_write"                        # 日志创建 / 编辑自己的
    TICKET_WRITE = "ticket_write"                  # 工单创建 / 编辑 / 流转
    COMMENT_WRITE = "comment_write"                # 评论
    CONTENT_VIEW = "content_view"                  # 查看内容, then by share level
    TOKEN_CREATE_PERSONAL = "token_create_personal"
    TOKEN_CREATE_SERVICE = "token_create_service"
    TOKEN_REVOKE_ANY = "token_revoke_any"
    WEBHOOK_MANAGE = "webhook_manage"


#: The matrix, one entry per role. §5.4 has no row for space management: it is
#: assigned to Admin here because creating a space and adding members to it *is*
#: granting L2 read access to whatever the space holds, which belongs with the
#: other access-granting powers rather than with "edit your own log". A space
#: owner can still manage that one space's membership — see
#: ``relay.app.accounts.spaces``, which is a per-object check, not a role.
ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.ADMIN: frozenset(
        {
            Capability.USER_MANAGE,
            Capability.DOMAIN_ALLOWLIST_MANAGE,
            Capability.AI_CONTEXT_CONFIG,
            Capability.SPACE_MANAGE,
            Capability.LOG_WRITE,
            Capability.TICKET_WRITE,
            Capability.COMMENT_WRITE,
            Capability.CONTENT_VIEW,
            Capability.TOKEN_CREATE_PERSONAL,
            Capability.TOKEN_CREATE_SERVICE,
            Capability.TOKEN_REVOKE_ANY,
            Capability.WEBHOOK_MANAGE,
        }
    ),
    Role.MEMBER: frozenset(
        {
            Capability.LOG_WRITE,
            Capability.TICKET_WRITE,
            Capability.COMMENT_WRITE,
            Capability.CONTENT_VIEW,
            # §5.4: "仅个人 token". No service token, no webhook endpoint, and
            # no revoking anybody else's — revoking a service token is how you
            # break an integration everyone depends on.
            Capability.TOKEN_CREATE_PERSONAL,
        }
    ),
    # A Guest reads, within the share levels below, and does nothing else.
    Role.GUEST: frozenset({Capability.CONTENT_VIEW}),
}


def capabilities_for(role: Role) -> frozenset[Capability]:
    """Total over ``Role`` — a role missing from the table is a bug, not a deny.

    Raising here rather than returning an empty set is deliberate: a new role
    added to the enum and forgotten in the matrix would otherwise silently
    become a Guest, which reads as working software.
    """
    try:
        return ROLE_CAPABILITIES[role]
    except KeyError:  # pragma: no cover - guards a future enum member
        raise AssertionError(f"role {role!r} has no entry in the §5.4 capability matrix") from None


def can(role: Role, capability: Capability) -> bool:
    return capability in capabilities_for(role)


#: LOG-6 evaluation order is *tenant filter → share level → role*; this is the
#: last step.
#:
#: **Admin reaches L0.** Design §6.3 defines the level as "仅作者 + Admin", which
#: is more specific than §5.4's coarse "按分享级别" row and therefore wins. Since
#: L0 is the most restrictive level, an Admin who reaches it necessarily reaches
#: every other one — anything else would make the ordering incoherent. So an
#: Admin is a whole-tenant reader by design, and the honest place to say that is
#: here, in the table, rather than in a special case further down.
#:
#: For Member and Guest, L0 stays absent: a private log is reachable by its
#: author, which is an ownership test rather than a role test.
_SHARE_LEVELS_BY_ROLE: dict[Role, frozenset[ShareLevel]] = {
    Role.ADMIN: frozenset(
        {ShareLevel.PRIVATE, ShareLevel.NAMED, ShareLevel.SPACE, ShareLevel.TENANT}
    ),
    Role.MEMBER: frozenset({ShareLevel.NAMED, ShareLevel.SPACE, ShareLevel.TENANT}),
    # S-6: **joining a space does not grant L2.** A Guest is in the tenant to
    # see specific things (L1) plus whatever is tenant-public (L3). Space
    # membership is a convenience for organising people; if it also granted
    # read access, "add the contractor to the team space" would quietly hand
    # over every space-shared log, and nobody would read it that way.
    Role.GUEST: frozenset({ShareLevel.NAMED, ShareLevel.TENANT}),
}


def share_levels_reachable_by(role: Role) -> frozenset[ShareLevel]:
    try:
        return _SHARE_LEVELS_BY_ROLE[role]
    except KeyError:  # pragma: no cover - guards a future enum member
        raise AssertionError(f"role {role!r} has no share-level entry (S-6)") from None


def role_reaches_share_level(role: Role, level: ShareLevel) -> bool:
    """Does the *role* permit reading something shared at ``level``?

    Not the whole answer for LOG-6: the caller still has to check the tenant
    (RLS does that), and for L1 that a grant exists, and for L2 that the reader
    is in the space. This answers only the role question.
    """
    return level in share_levels_reachable_by(role)


#: What a token scope lets its holder do (API-1's four coarse scopes).
SCOPE_CAPABILITIES: dict[TokenScope, frozenset[Capability]] = {
    TokenScope.TICKETS_READ: frozenset({Capability.CONTENT_VIEW}),
    TokenScope.TICKETS_WRITE: frozenset({Capability.CONTENT_VIEW, Capability.TICKET_WRITE}),
    TokenScope.COMMENTS_WRITE: frozenset({Capability.CONTENT_VIEW, Capability.COMMENT_WRITE}),
    TokenScope.META_READ: frozenset({Capability.CONTENT_VIEW}),
}


def effective_capabilities(
    role: Role | None, scopes: frozenset[TokenScope] | None
) -> frozenset[Capability]:
    """What the caller may actually do, given a role and/or a token's scopes.

    Three cases, and the third is the one that matters:

    * ``role`` only — a browser session. The role's capabilities.
    * ``scopes`` only — a **service** token. It has no user and therefore no
      role, so its scopes are the whole story; note that no scope maps to
      ``USER_MANAGE`` or ``WEBHOOK_MANAGE``, so a service token cannot
      administer the tenant however it was created.
    * both — a **personal** token. The **intersection**. A token minted while
      its owner was a Member and used after they were demoted to Guest grants
      nothing: the role is re-read per request, not frozen into the token.
      Without this, "change their role" would leave a working credential behind
      and R-2's monthly review would be checking the wrong thing.
    """
    if scopes is None:
        return capabilities_for(role) if role is not None else frozenset()
    granted = _capabilities_of(scopes)
    if role is None:
        return granted
    return granted & capabilities_for(role)


def _capabilities_of(scopes: frozenset[TokenScope]) -> frozenset[Capability]:
    granted: set[Capability] = set()
    for scope in scopes:
        granted |= SCOPE_CAPABILITIES[scope]
    return frozenset(granted)


@dataclass(frozen=True, slots=True)
class TokenRequest:
    """A request to mint an API token, as AC-4 needs to judge it.

    Lifted out of the ORM so the rule is testable without a database — the same
    reason ``AllowlistedDomain`` exists in :mod:`relay.domain.residency`.
    """

    principal_type: PrincipalType
    #: The user the token acts as. Must be None for a service principal.
    principal_user_id: uuid.UUID | None
    scopes: frozenset[TokenScope]


#: §5.4, row "创建 / 吊销 API token".
GUEST_REFUSAL = "访客不能创建 API token，请联系管理员。"
SERVICE_TOKEN_REFUSAL = "只有管理员可以创建服务 token。"
SERVICE_TOKEN_HAS_USER = "服务 token 不能绑定到某个用户。"
PERSONAL_TOKEN_NEEDS_USER = "个人 token 必须绑定到创建者本人。"
PERSONAL_TOKEN_NOT_YOURS = "不能为其他用户创建个人 token。"
NO_SCOPES = "至少要选择一个权限范围。"
SCOPE_EXCEEDS_ROLE = "token 的权限范围不能超过你自己的权限。"


def token_request_refusal(
    actor_role: Role, actor_user_id: uuid.UUID | None, request: TokenRequest
) -> str | None:
    """The reason this token may not be created, or None if it may.

    Returns a message rather than raising so that the same rule can answer "may
    I show this button?" and "may I run this request?" — a form that offers an
    action the service layer will refuse is its own kind of bug.

    The rule that is *not* in §5.4, and is decided here: **nobody mints a
    personal token bound to another user, Admin included.** The matrix gives
    Admin the token power, and read literally that would let an Admin issue a
    credential that acts as a colleague — impersonation, with every audit row it
    produces attributed to the wrong person. An Admin who needs machine access
    creates a service token, which is attributable by construction. Revoking
    somebody's token stays an Admin power (``TOKEN_REVOKE_ANY``); creating one
    as them does not.
    """
    if request.principal_type is PrincipalType.SERVICE:
        if not can(actor_role, Capability.TOKEN_CREATE_SERVICE):
            return GUEST_REFUSAL if actor_role is Role.GUEST else SERVICE_TOKEN_REFUSAL
        if request.principal_user_id is not None:
            return SERVICE_TOKEN_HAS_USER
    else:
        if not can(actor_role, Capability.TOKEN_CREATE_PERSONAL):
            return GUEST_REFUSAL
        if request.principal_user_id is None:
            return PERSONAL_TOKEN_NEEDS_USER
        if actor_user_id is None or request.principal_user_id != actor_user_id:
            return PERSONAL_TOKEN_NOT_YOURS

    if not request.scopes:
        return NO_SCOPES

    # A Member cannot hand a token more than they have themselves — the check
    # bites the moment a role is narrowed, or a scope is added later that Member
    # does not carry.
    wanted = _capabilities_of(request.scopes)
    if request.principal_type is PrincipalType.USER and not wanted <= capabilities_for(actor_role):
        return SCOPE_EXCEEDS_ROLE
    return None
