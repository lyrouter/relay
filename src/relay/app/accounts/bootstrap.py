"""AC-9 · deploy-time one-shot initialization (S-4).

**Not "the first user to register becomes Admin".** On an internal network that
is a real takeover risk: the platform is reachable before anyone has been told
it exists, and whoever finds it first owns it. The decision (S-4) is a
credentialed init step in the deployment handbook instead.

Runs on the ``relay_system`` connection, and that is forced rather than chosen:
``FORCE ROW LEVEL SECURITY`` binds ``relay_owner`` too, so no role except the
BYPASSRLS one can insert the very first tenant — there is no tenant context to
set yet. Which is why this is an operations step and not a migration.

Idempotent by tenant slug: re-running it is a no-op, so a deployment script that
retries does not create a second Admin.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app.tickets.ai_context import seed_field_config
from relay.domain import passwords
from relay.domain.enums import Role, TenantStatus, UserStatus
from relay.domain.residency import email_domain, normalize_domain, normalize_email
from relay.infra.db.models import Tenant, TenantEmailDomain, User
from relay.infra.db.system_repository import SystemRepository
from relay.infra.security.passwords import hash_password


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    tenant_name: str
    tenant_slug: str
    admin_email: str
    admin_password: str
    #: Domains that may self-register into this tenant. Defaults to the admin's
    #: own domain, which is almost always what is wanted and is the only one we
    #: can infer safely.
    allowed_domains: tuple[str, ...] = ()
    auto_join: bool = True
    default_role: Role = Role.MEMBER
    timezone: str = "Asia/Shanghai"
    #: TKT-2 · §7.3. Domain scopes whose AI-context fields this tenant gets on
    #: top of the generic set. ``("gateway",)`` for the AI gateway team — the
    #: first tenant, and the only one those fields mean anything to. Left empty
    #: by default so a second tenant does not silently inherit them: §7.3's test
    #: is "could a team with no gateway of its own fill this in?", and the whole
    #: point of the gate is that the answer stays no.
    domain_scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    tenant_id: uuid.UUID
    admin_user_id: uuid.UUID
    domains: tuple[str, ...]
    created: bool


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AddDomainsResult:
    tenant_id: uuid.UUID
    added: tuple[str, ...]
    already_present: tuple[str, ...]


def bootstrap_tenant(
    request: BootstrapRequest, *, now: dt.datetime | None = None
) -> BootstrapResult:
    now = now or dt.datetime.now(dt.UTC)
    admin_email = normalize_email(request.admin_email)

    # Validate before touching the database: a deployment that fails here should
    # fail with a fixable message, not a half-created tenant.
    passwords.validate(request.admin_password, email=admin_email)
    domains = _normalized_domains(
        request.allowed_domains or (email_domain(admin_email),)
    )
    if not request.tenant_slug.isascii() or not request.tenant_slug.replace("-", "").isalnum():
        raise BootstrapError(
            f"tenant slug {request.tenant_slug!r} must be ASCII alphanumeric with hyphens — "
            "it appears in every permalink (S-12)."
        )

    def work(session):
        existing = session.scalars(
            select(Tenant).where(Tenant.slug == request.tenant_slug)
        ).first()
        if existing is not None:
            admin_id = session.scalars(
                select(User.id).where(
                    User.tenant_id == existing.id, User.email == admin_email
                )
            ).first()
            if admin_id is None:
                # A tenant with this slug but not this admin: refuse rather than
                # add a second Admin to somebody else's tenant.
                raise BootstrapError(
                    f"tenant {request.tenant_slug!r} already exists and {admin_email} is not "
                    "its admin. Refusing to add another Admin — use the invitation path."
                )
            return BootstrapResult(existing.id, admin_id, domains, created=False)

        # A domain belongs to exactly one tenant (S-3), enforced by a unique
        # index. Check first so the failure names the conflict.
        taken = session.scalars(
            select(TenantEmailDomain.domain).where(TenantEmailDomain.domain.in_(domains))
        ).all()
        if taken:
            raise BootstrapError(
                f"these domains already belong to another tenant: {sorted(taken)}. "
                "Domain ↔ tenant is one-to-one (S-3)."
            )

        tenant = Tenant(
            name=request.tenant_name,
            slug=request.tenant_slug,
            status=TenantStatus.ACTIVE,
            timezone=request.timezone,
        )
        session.add(tenant)
        session.flush()

        admin = User(
            tenant_id=tenant.id,
            email=admin_email,
            password_hash=hash_password(request.admin_password),
            # Verified by construction: this account was created by whoever holds
            # the deployment credentials, not by someone claiming an address.
            email_verified_at=now,
            status=UserStatus.ACTIVE,
            role=Role.ADMIN,
            display_name=admin_email.split("@")[0],
            password_changed_at=now,
        )
        session.add(admin)
        session.add_all(
            TenantEmailDomain(
                tenant_id=tenant.id,
                domain=domain,
                default_role=request.default_role,
                auto_join=request.auto_join,
            )
            for domain in domains
        )
        session.flush()

        # TKT-2. Seeded here rather than by a migration because the row set
        # depends on the tenant: a migration would have to guess which tenants
        # get the gateway fields, and guessing wrong is how a gated field
        # becomes a generic one.
        seed_field_config(session, tenant.id, domain_scopes=request.domain_scopes)
        return BootstrapResult(tenant.id, admin.id, domains, created=True)

    # run_creating_tenant, not run: the audit row has to be filed under the
    # tenant this call creates, and that tenant does not exist when the call
    # starts. See SystemRepository.run_creating_tenant for why the audit is
    # in-transaction here and committed ahead of the work everywhere else.
    return SystemRepository().run_creating_tenant(
        "bootstrap_tenant",
        f"AC-9 deploy-time initialization of tenant {request.tenant_slug!r}",
        work,
        tenant_id_of=lambda result: result.tenant_id,
    )


def add_allowed_domains(
    tenant_slug: str,
    domains: tuple[str, ...],
    *,
    auto_join: bool | None = None,
    default_role: Role | None = None,
) -> AddDomainsResult:
    """Admit more email domains to an existing tenant (AC-9).

    Bootstrap only writes the allowlist at creation, and re-running it is a
    no-op so a retry cannot create a second Admin. Adding a company domain
    three months later is a different operation, and this is it.

    New rows inherit ``auto_join`` / ``default_role`` from a domain the tenant
    already has, unless the caller overrides them. Domain ↔ tenant stays
    one-to-one: a domain that already belongs to somebody else is refused.
    """
    normalized = _normalized_domains(domains)
    if not normalized:
        raise BootstrapError("at least one domain is required")

    repo = SystemRepository()
    tenants = repo.list_tenants(
        f"resolve tenant {tenant_slug!r} before adding allowlisted domains"
    )
    tenant = next((row for row in tenants if row.slug == tenant_slug), None)
    if tenant is None:
        raise BootstrapError(
            f"no tenant {tenant_slug!r} — run bootstrap_tenant.py first."
        )
    tenant_id = tenant.id

    def work(session):
        claimed = {
            row.domain: row.tenant_id
            for row in session.scalars(
                select(TenantEmailDomain).where(TenantEmailDomain.domain.in_(normalized))
            )
        }
        taken = sorted(domain for domain, owner in claimed.items() if owner != tenant_id)
        if taken:
            raise BootstrapError(
                f"these domains already belong to another tenant: {taken}. "
                "Domain ↔ tenant is one-to-one (S-3)."
            )

        existing_rows = list(
            session.scalars(
                select(TenantEmailDomain).where(TenantEmailDomain.tenant_id == tenant_id)
            )
        )
        present = {row.domain for row in existing_rows}
        template = existing_rows[0] if existing_rows else None
        if default_role is not None:
            role = default_role
        elif template is not None:
            role = template.default_role
        else:
            role = Role.MEMBER
        if auto_join is not None:
            join = auto_join
        elif template is not None:
            join = template.auto_join
        else:
            join = True

        added = tuple(domain for domain in normalized if domain not in present)
        session.add_all(
            TenantEmailDomain(
                tenant_id=tenant_id,
                domain=domain,
                default_role=role,
                auto_join=join,
            )
            for domain in added
        )
        return AddDomainsResult(
            tenant_id=tenant_id,
            added=added,
            already_present=tuple(domain for domain in normalized if domain in present),
        )

    return repo.run(
        "add_allowed_domains",
        f"AC-9 add allowlisted domains {normalized} to tenant {tenant_slug!r}",
        work,
        tenant_id=tenant_id,
    )


def _normalized_domains(raw: tuple[str, ...]) -> tuple[str, ...]:
    """Deduped, lowercased, and rejected if it would not survive ``email_domain``."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        try:
            domain = normalize_domain(item)
        except ValueError as exc:
            raise BootstrapError(str(exc)) from exc
        if domain not in seen:
            seen.add(domain)
            out.append(domain)
    return tuple(out)
