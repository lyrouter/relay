"""AC-1 · the pre-tenant path stays narrow.

``PreTenantRepository`` runs on the BYPASSRLS connection and is *not* audited
per call, because signup is a public endpoint and auditing every hit would
drown the cross-tenant reads that matter. Those two facts together mean its
surface is the whole security argument, so the surface is a test.
"""

from __future__ import annotations

import uuid

import pytest

from relay.domain.enums import Role
from relay.infra.db.models import TenantEmailDomain
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.db.session import tenant_session

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

#: Widening this is exactly what review should catch. Adding a method here in
#: the same PR as the method itself is the point — it forces the question
#: "does this need to be readable before authentication?" to be asked out loud.
ALLOWED_SURFACE = {"session", "resolve_domain", "email_taken"}


def test_surface_is_frozen():
    actual = {m for m in dir(PreTenantRepository) if not m.startswith("_")}
    assert actual == ALLOWED_SURFACE, (
        "PreTenantRepository runs unaudited on a BYPASSRLS connection. Every method "
        "here is readable by anyone who can reach the signup endpoint."
    )


def test_resolve_domain_returns_only_the_decision_inputs(tenant_a):
    with tenant_session(context_for(tenant_a)) as session:
        session.add(
            TenantEmailDomain(tenant_id=tenant_a, domain="alpha.test", default_role=Role.MEMBER)
        )
        session.commit()

    repo = PreTenantRepository()
    with repo.session() as session:
        found = repo.resolve_domain(session, "ALPHA.TEST")
    assert found is not None
    assert found.tenant_id == tenant_a
    assert found.allowlist.default_role is Role.MEMBER
    assert found.allowlist.auto_join is True
    # Nothing about the tenant beyond what the decision needs.
    assert not hasattr(found.allowlist, "name")


def test_subdomains_do_not_match(tenant_a):
    """`evil.alpha.test` matching `alpha.test` is one domain takeover away from
    an open door."""
    with tenant_session(context_for(tenant_a)) as session:
        session.add(TenantEmailDomain(tenant_id=tenant_a, domain="alpha.test"))
        session.commit()

    repo = PreTenantRepository()
    with repo.session() as session:
        assert repo.resolve_domain(session, "evil.alpha.test") is None
        assert repo.resolve_domain(session, "notalpha.test") is None


def test_unknown_domain_resolves_to_nothing():
    repo = PreTenantRepository()
    with repo.session() as session:
        assert repo.resolve_domain(session, "nowhere.example") is None


def test_email_taken_answers_a_bool_and_nothing_more(tenant_a, user_factory):
    user_factory(tenant_a, "a@alpha.test")
    repo = PreTenantRepository()
    with repo.session() as session:
        assert repo.email_taken(session, tenant_a, "a@alpha.test") is True
        assert repo.email_taken(session, tenant_a, "nobody@alpha.test") is False
        assert repo.email_taken(session, uuid.uuid4(), "a@alpha.test") is False
