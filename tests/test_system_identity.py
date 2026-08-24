"""S-20 · the identity a scheduled job runs as.

The 90-day purge (S-8) needed ``USER_MANAGE`` and a scheduler has no session, so
for a while it could not run at all. The decision was to give the scheduler an
identity rather than let it borrow an Admin's account.

What is worth testing about an identity that authorizes itself is not the
authorization — it is the two walls around it:

* **it cannot serve a request.** Origin must be ``SYSTEM``; a system actor
  arriving over the web or the API is refused, whatever the actor type says.
* **it is not a person.** A system run carrying a user id is refused, so an
  audit row can never name somebody who was asleep.

Plus the thing that made all this necessary: the purge actually runs, end to
end, with nobody logged in.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select, text

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.authz import SYSTEM_CAPABILITIES, actor_principal, system_principal
from relay.app.errors import PermissionDenied
from relay.app.logs.retention import (
    count_old_versions,
    purge_every_tenant,
    system_context,
)
from relay.app.logs.service import LogService
from relay.context import ActorType, Origin, TenantContext, tenant_scope
from relay.domain.enums import Role, UserStatus
from relay.domain.permissions import Capability
from relay.infra.db.engine import system_engine
from relay.infra.db.models import AuditLog, LogVersion
from relay.infra.db.session import tenant_session

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
NOW = dt.datetime(2026, 8, 24, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def gateway():
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=PASSWORD,
        )
    )


@pytest.fixture
def author(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "lisa@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )


def backdate_versions(log_id, days: int) -> None:
    """Age the version rows.

    Through the BYPASSRLS connection, not the owner one: ``FORCE ROW LEVEL
    SECURITY`` binds the owner too, so an owner UPDATE would need
    ``app.tenant_id`` set — which is the property MT-3 exists to have, and not
    something a fixture should be arranging.
    """
    with system_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE log_version SET created_at = created_at - make_interval(days => :d) "
                "WHERE log_id = :log_id"
            ),
            {"d": days, "log_id": log_id},
        )


# ------------------------------------------------------------------ the walls


def test_a_system_identity_cannot_serve_a_web_request(gateway):
    """The wall S-20 asked for. The HTTP layer never builds one — a resolved
    session is always ``ActorType.USER`` — and this is the check that still
    holds if that ever changes."""
    ctx = TenantContext(
        tenant_id=gateway.tenant_id,
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        origin=Origin.WEB,
    )
    with tenant_scope(ctx):
        with pytest.raises(PermissionDenied):
            system_principal()


def test_a_system_identity_cannot_serve_an_api_request(gateway):
    ctx = TenantContext(
        tenant_id=gateway.tenant_id,
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        origin=Origin.API,
    )
    with tenant_scope(ctx):
        with pytest.raises(PermissionDenied):
            system_principal()


def test_a_system_run_may_not_borrow_a_user(gateway):
    """Option ① of the decision, refused in code: if the scheduler could carry
    an Admin's id, every audit row it wrote would name a person who was not
    there — which is worse than no row, because it is evidence of the wrong
    thing."""
    ctx = TenantContext(
        tenant_id=gateway.tenant_id,
        actor_id=gateway.admin_user_id,
        actor_type=ActorType.SYSTEM,
        origin=Origin.SYSTEM,
    )
    with tenant_scope(ctx):
        with pytest.raises(PermissionDenied):
            system_principal()


def test_a_user_context_cannot_ask_for_the_system_principal(gateway):
    with tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id)):
        with pytest.raises(PermissionDenied):
            system_principal()


def test_the_system_principal_holds_exactly_what_the_list_says(gateway):
    """A short, closed list is the only control there is over an identity that
    grants itself its own capabilities — so pin that it is not "everything an
    Admin can do"."""
    with tenant_scope(system_context(gateway.tenant_id)):
        actor = system_principal()
    assert actor.capabilities == SYSTEM_CAPABILITIES
    assert actor.can(Capability.USER_MANAGE)
    assert not actor.can(Capability.LOG_WRITE)
    assert not actor.can(Capability.TICKET_WRITE)
    assert actor.user_id is None
    assert actor.role is None


def test_a_use_case_does_not_need_to_know_who_called_it(gateway):
    """``actor_principal`` dispatches, so a service method takes a scheduled run
    and a request through the same line of code."""
    with tenant_session(system_context(gateway.tenant_id)) as session:
        assert actor_principal(session).can(Capability.USER_MANAGE)


# --------------------------------------------------------------- the purge runs


def test_the_purge_runs_with_nobody_logged_in(gateway, author):
    """The thing that did not work before S-20: no session, no user, and the
    cleanup still happens — per tenant, under RLS."""
    with tenant_scope(context_for(gateway.tenant_id, author)):
        log = LogService().create("排查记录", "第一版", now=NOW)
        LogService().save(log.id, body="第二版", now=NOW)
        LogService().save(log.id, body="第三版", now=NOW)
    backdate_versions(log.id, 200)

    assert purge_every_tenant() == {"gateway": 2}

    with tenant_session(context_for(gateway.tenant_id)) as session:
        kept = session.scalars(
            select(LogVersion.version_no).where(LogVersion.log_id == log.id)
        ).all()
    # S-8's second half: the latest version of every log is kept permanently,
    # even when it is older than the window.
    assert list(kept) == [3]


def test_the_purge_files_its_audit_row_as_system(gateway, author):
    with tenant_scope(context_for(gateway.tenant_id, author)):
        log = LogService().create("排查记录", "第一版", now=NOW)
        LogService().save(log.id, body="第二版", now=NOW)
    backdate_versions(log.id, 200)
    purge_every_tenant()

    with tenant_session(context_for(gateway.tenant_id)) as session:
        row = session.scalars(
            select(AuditLog).where(AuditLog.action == "log.versions_purged")
        ).one()
    assert row.actor_type is ActorType.SYSTEM
    assert row.origin is Origin.SYSTEM
    assert row.actor_id is None
    assert row.after["deleted"] == 1


def test_a_purge_that_deletes_nothing_writes_no_audit_row(gateway, author):
    """A nightly row saying "0" for months is how a log stops being read."""
    with tenant_scope(context_for(gateway.tenant_id, author)):
        log = LogService().create("排查记录", "第一版", now=NOW)
        LogService().save(log.id, body="第二版", now=NOW)

    assert purge_every_tenant() == {"gateway": 0}
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert (
            session.scalars(
                select(AuditLog).where(AuditLog.action == "log.versions_purged")
            ).all()
            == []
        )


def test_the_dry_run_counts_what_the_real_run_deletes(gateway, author):
    """``--dry-run`` shares the selection with the real thing, so a rehearsal
    cannot report zero while the run deletes thousands."""
    with tenant_scope(context_for(gateway.tenant_id, author)):
        log = LogService().create("排查记录", "第一版", now=NOW)
        LogService().save(log.id, body="第二版", now=NOW)
        LogService().save(log.id, body="第三版", now=NOW)
    backdate_versions(log.id, 200)

    assert purge_every_tenant(dry_run=True) == {"gateway": 2}
    with tenant_scope(system_context(gateway.tenant_id)):
        assert count_old_versions() == 2  # still there: the dry run deleted nothing
    assert purge_every_tenant() == {"gateway": 2}


def test_an_admin_can_still_run_it_by_hand(gateway, author):
    """S-20 added an identity; it did not take the operation away from the Admin
    who is standing there at 3am."""
    with tenant_scope(context_for(gateway.tenant_id, author)):
        log = LogService().create("排查记录", "第一版", now=NOW)
        LogService().save(log.id, body="第二版", now=NOW)
    backdate_versions(log.id, 200)

    from relay.app.logs.retention import purge_old_versions

    with tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id)):
        assert purge_old_versions() == 1

    with tenant_session(context_for(gateway.tenant_id)) as session:
        row = session.scalars(
            select(AuditLog).where(AuditLog.action == "log.versions_purged")
        ).one()
    assert row.actor_id == gateway.admin_user_id
    assert row.actor_type is ActorType.USER


def test_a_member_cannot_run_it(gateway, author):
    with tenant_scope(context_for(gateway.tenant_id, author)):
        from relay.app.logs.retention import purge_old_versions

        with pytest.raises(PermissionDenied):
            purge_old_versions()
