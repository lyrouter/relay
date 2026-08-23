"""TKT-1 / TKT-3 / TKT-8 · the ticket use cases against a real database.

The properties worth pinning here are the ones that are cheap now and expensive
later: numbering is per tenant, ``rev`` gates every mutation, status never moves
without a history row, and a repeated external create returns the ticket that
already exists instead of a second one.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from relay.app.accounts.administration import AdminService
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from relay.app.notifications import inbox, unread_count
from relay.app.tickets.metadata import BoardMetadataService
from relay.app.tickets.service import (
    ExternalRef,
    NewTicket,
    TicketFilters,
    TicketService,
)
from relay.context import ActorType, Origin, TenantContext, tenant_scope
from relay.domain.ai_context import GATEWAY_SCOPE
from relay.domain.enums import Priority, Role, TicketStatus, TicketType, UserStatus
from relay.infra.db.models import AuditLog, Ticket, TicketStatusHistory
from relay.infra.db.session import tenant_session

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
NOW = dt.datetime(2026, 8, 24, 10, 0, tzinfo=dt.UTC)
S = TicketStatus


@pytest.fixture
def gateway():
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=PASSWORD,
            domain_scopes=(GATEWAY_SCOPE,),
        )
    )


@pytest.fixture
def member(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "dev@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )


@pytest.fixture
def guest(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "contractor@zerosone.test", role=Role.GUEST, status=UserStatus.ACTIVE
    )


def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


def as_user(gateway, user_id):
    return tenant_scope(context_for(gateway.tenant_id, user_id))


def a_bug(**overrides) -> NewTicket:
    fields = {"type": TicketType.BUG, "title": "网关 502"} | overrides
    return NewTicket(**fields)


def audit_actions(tenant_id):
    with tenant_session(context_for(tenant_id)) as session:
        return list(session.scalars(select(AuditLog.action)))


# ------------------------------------------------------------------- create


def test_a_ticket_is_created_with_number_one_and_rev_one(gateway):
    with as_admin(gateway):
        view = TicketService().create(a_bug(), now=NOW)

    assert view.number == 1
    assert view.key == "RL-1"
    assert view.status is S.TODO
    assert view.priority is Priority.P2
    assert view.rev == 1
    assert view.reporter_id == gateway.admin_user_id
    assert "ticket.created" in audit_actions(gateway.tenant_id)


def test_numbers_increment_within_the_tenant(gateway):
    with as_admin(gateway):
        service = TicketService()
        keys = [service.create(a_bug(title=f"第 {i} 个"), now=NOW).key for i in range(3)]
    assert keys == ["RL-1", "RL-2", "RL-3"]


def test_each_tenant_numbers_from_one(gateway):
    """RL-1 means "the first ticket in *this* tenant" (S-12). The MAX runs under
    RLS, so this falls out of the policy rather than out of a WHERE clause."""
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with as_admin(gateway):
        TicketService().create(a_bug(), now=NOW)
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        assert TicketService().create(a_bug(), now=NOW).number == 1


def test_the_opening_history_row_is_written(gateway):
    """Otherwise a ticket's first status is the only one with no record of who
    set it — and §8.4's actor_type is unreconstructable for it."""
    with as_admin(gateway):
        view = TicketService().create(a_bug(), now=NOW)
        history = TicketService().history(view.id)

    assert len(history) == 1
    assert history[0].from_status is None
    assert history[0].to_status is S.TODO
    assert history[0].actor_id == gateway.admin_user_id


def test_a_blank_title_is_refused(gateway):
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            TicketService().create(a_bug(title="   "), now=NOW)


def test_a_guest_cannot_create_a_ticket(gateway, guest):
    with as_user(gateway, guest):
        with pytest.raises(PermissionDenied):
            TicketService().create(a_bug(), now=NOW)


def test_a_member_can(gateway, member):
    with as_user(gateway, member):
        assert TicketService().create(a_bug(), now=NOW).number == 1


def test_a_deactivated_reporter_cannot_create(gateway, member):
    with as_admin(gateway):
        AdminService().deactivate_user(member)
    with as_user(gateway, member):
        with pytest.raises(PermissionDenied):
            TicketService().create(a_bug(), now=NOW)


def test_an_assignee_from_another_tenant_is_not_found(gateway):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with as_admin(gateway):
        with pytest.raises(NotFound):
            TicketService().create(a_bug(assignee_id=other.admin_user_id), now=NOW)


def test_a_deactivated_user_cannot_be_assigned(gateway, member):
    with as_admin(gateway):
        AdminService().deactivate_user(member)
        with pytest.raises(NotFound):
            TicketService().create(a_bug(assignee_id=member), now=NOW)


def test_an_unknown_label_or_iteration_is_refused(gateway):
    with as_admin(gateway):
        with pytest.raises(NotFound):
            TicketService().create(a_bug(label_ids=(uuid.uuid4(),)), now=NOW)
        with pytest.raises(NotFound):
            TicketService().create(a_bug(iteration_id=uuid.uuid4()), now=NOW)


# --------------------------------------------------------------- ai_context


def test_a_declared_field_is_stored(gateway):
    with as_admin(gateway):
        view = TicketService().create(
            a_bug(ai_context={"trace_id": ["t-1"], "error_class": "timeout"}), now=NOW
        )
    assert view.ai_context == {"trace_id": ["t-1"], "error_class": "timeout"}


def test_the_gateway_tenant_can_write_its_gated_field(gateway):
    with as_admin(gateway):
        view = TicketService().create(a_bug(ai_context={"routing_policy": "canary"}), now=NOW)
    assert view.ai_context == {"routing_policy": "canary"}


def test_a_tenant_without_the_scope_cannot(gateway):
    """The §7.3 gate, reached through the use case rather than the validator."""
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        with pytest.raises(ValidationFailed) as refused:
            TicketService().create(a_bug(ai_context={"routing_policy": "canary"}), now=NOW)
    assert "routing_policy" in str(refused.value)


def test_an_undeclared_key_never_reaches_the_column(gateway):
    """§7.3: validated against the config, **not** stored as arbitrary JSON."""
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            TicketService().create(a_bug(ai_context={"made_up": "1"}), now=NOW)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.scalars(select(Ticket)).all() == []


# ------------------------------------------------------------------ pr_url


def test_a_pr_link_is_stored_as_given(gateway):
    with as_admin(gateway):
        view = TicketService().create(
            a_bug(pr_url="https://github.com/acme/relay/pull/7"), now=NOW
        )
    assert view.pr_url == "https://github.com/acme/relay/pull/7"


def test_a_javascript_url_is_refused(gateway):
    """TKT-8 says a plain link, and a plain link is still rendered as an anchor:
    ``javascript:`` in an href is the cheapest stored XSS there is."""
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            TicketService().create(a_bug(pr_url="javascript:alert(1)"), now=NOW)


# ------------------------------------------------------------ external_ref


def test_a_repeated_external_create_returns_the_existing_ticket(gateway):
    """API-3. An alert that fires twice must not produce two tickets, and the
    second caller needs to be told which one to look at."""
    ref = ExternalRef(system="alertmanager", external_id="evt-9")
    with as_admin(gateway):
        service = TicketService()
        first = service.create(a_bug(external_ref=ref), now=NOW)
        second = service.create(a_bug(title="同一个告警", external_ref=ref), now=NOW)

    assert second.id == first.id
    assert second.deduped is True
    assert first.deduped is False
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert len(session.scalars(select(Ticket)).all()) == 1


def test_the_dedupe_is_per_tenant(gateway):
    """``UNIQUE (tenant_id, system, external_id)``: two tenants integrating with
    the same alertmanager must not collide."""
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    ref = ExternalRef(system="alertmanager", external_id="evt-9")
    with as_admin(gateway):
        TicketService().create(a_bug(external_ref=ref), now=NOW)
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        assert TicketService().create(a_bug(external_ref=ref), now=NOW).deduped is False


def test_a_different_external_id_is_a_different_ticket(gateway):
    with as_admin(gateway):
        service = TicketService()
        one = service.create(
            a_bug(external_ref=ExternalRef("alertmanager", "evt-1")), now=NOW
        )
        two = service.create(
            a_bug(external_ref=ExternalRef("alertmanager", "evt-2")), now=NOW
        )
    assert one.id != two.id


# ------------------------------------------------------------------ update


def test_a_patch_bumps_the_rev(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        updated = service.update(created.id, expected_rev=created.rev, title="网关 504")
    assert updated.title == "网关 504"
    assert updated.rev == created.rev + 1


def test_a_stale_rev_is_a_conflict_carrying_the_current_one(gateway):
    """API-3's 409. The client re-reads exactly once rather than polling."""
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        service.update(created.id, expected_rev=created.rev, title="第一次")
        with pytest.raises(Conflict) as refused:
            service.update(created.id, expected_rev=created.rev, title="第二次")

    assert refused.value.detail["rev"] == created.rev + 1


def test_concurrent_patches_produce_one_winner(gateway):
    """The whole point of optimistic concurrency: the loser is told, not
    silently overwritten."""
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        service.update(created.id, expected_rev=1, priority=Priority.P0)
        with pytest.raises(Conflict):
            service.update(created.id, expected_rev=1, priority=Priority.P3)
        assert service.get(created.id).priority is Priority.P0


def test_absent_means_unchanged_and_none_means_clear(gateway, member):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(assignee_id=member, description="原始描述"), now=NOW)
        # Patching only the title leaves the assignee alone...
        one = service.update(created.id, expected_rev=1, title="改了标题")
        assert one.assignee_id == member
        assert one.description == "原始描述"
        # ...and an explicit None clears it.
        two = service.update(created.id, expected_rev=one.rev, assignee_id=None)
        assert two.assignee_id is None


def test_ai_context_replaces_rather_than_merges(gateway):
    """One JSONB field, so a patch replaces it. Merge semantics would leave no
    way to remove a key — and these fields are written by external systems that
    get things wrong and need to be able to take them back."""
    with as_admin(gateway):
        service = TicketService()
        created = service.create(
            a_bug(ai_context={"trace_id": ["t-1"], "error_class": "timeout"}), now=NOW
        )
        updated = service.update(created.id, expected_rev=1, ai_context={"trace_id": ["t-2"]})
    assert updated.ai_context == {"trace_id": ["t-2"]}


def test_labels_are_replaced_wholesale(gateway):
    with as_admin(gateway):
        board = BoardMetadataService()
        one = board.create_label("网关")
        two = board.create_label("紧急", "#ff0000")
        service = TicketService()
        created = service.create(a_bug(label_ids=(one.id,)), now=NOW)
        assert created.label_ids == (one.id,)
        updated = service.update(created.id, expected_rev=1, label_ids=(two.id,))
    assert updated.label_ids == (two.id,)


def test_a_guest_cannot_patch(gateway, guest):
    with as_admin(gateway):
        created = TicketService().create(a_bug(), now=NOW)
    with as_user(gateway, guest):
        with pytest.raises(PermissionDenied):
            TicketService().update(created.id, expected_rev=1, title="改了")


def test_another_tenants_ticket_is_not_found(gateway):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        theirs = TicketService().create(a_bug(), now=NOW)
    with as_admin(gateway):
        with pytest.raises(NotFound):
            TicketService().update(theirs.id, expected_rev=1, title="不该改到")


# -------------------------------------------------------------- transitions


def test_a_transition_writes_history_with_actor_type_and_origin(gateway):
    """§8.4. Whether a person dragged a card or an external system called the
    API cannot be reconstructed after the fact, which is why the column exists
    before the API does."""
    ctx = TenantContext(
        tenant_id=gateway.tenant_id,
        actor_id=gateway.admin_user_id,
        actor_type=ActorType.INTEGRATION,
        origin=Origin.API,
    )
    with tenant_scope(ctx):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        service.transition(created.id, S.IN_PROGRESS, expected_rev=created.rev, now=NOW)
        history = service.history(created.id)

    assert history[-1].to_status is S.IN_PROGRESS
    assert history[-1].actor_type is ActorType.INTEGRATION
    assert history[-1].origin is Origin.API


def test_a_transition_bumps_the_rev(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        moved = service.transition(created.id, S.IN_PROGRESS, expected_rev=1, now=NOW)
    assert moved.rev == 2


def test_a_stale_rev_blocks_a_transition_too(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        service.transition(created.id, S.IN_PROGRESS, expected_rev=1, now=NOW)
        with pytest.raises(Conflict):
            service.transition(created.id, S.IN_REVIEW, expected_rev=1, now=NOW)


def test_an_illegal_transition_is_refused_with_the_legal_ones(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        with pytest.raises(ValidationFailed) as refused:
            service.transition(created.id, S.DONE, expected_rev=1, now=NOW)
    assert str(S.IN_PROGRESS) in str(refused.value)


def test_blocked_requires_a_reason_and_records_it(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        with pytest.raises(ValidationFailed):
            service.transition(created.id, S.BLOCKED, expected_rev=1, now=NOW)
        service.transition(
            created.id, S.BLOCKED, expected_rev=1, reason="等上游修复", now=NOW
        )
        assert service.history(created.id)[-1].reason == "等上游修复"


def test_a_blocked_ticket_resumes_to_where_it_came_from(gateway):
    """§7.2, end to end: the resume target is read from the history row that
    entered Blocked, so nothing has to store it twice."""
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        service.transition(created.id, S.IN_PROGRESS, expected_rev=1, now=NOW)
        service.transition(created.id, S.BLOCKED, expected_rev=2, reason="等接口", now=NOW)
        resumed = service.transition(created.id, S.IN_PROGRESS, expected_rev=3, now=NOW)
    assert resumed.status is S.IN_PROGRESS


def test_a_blocked_ticket_cannot_resume_somewhere_it_never_was(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        service.transition(created.id, S.BLOCKED, expected_rev=1, reason="等接口", now=NOW)
        with pytest.raises(ValidationFailed):
            service.transition(created.id, S.IN_REVIEW, expected_rev=2, now=NOW)


def test_the_full_forward_path_works(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        rev = created.rev
        for target in (S.IN_PROGRESS, S.IN_REVIEW, S.DONE):
            rev = service.transition(created.id, target, expected_rev=rev, now=NOW).rev
        assert service.get(created.id).status is S.DONE


def test_status_only_moves_through_transition(gateway):
    """``update`` has no status parameter, by construction: a status change with
    no ``ticket_status_history`` row is exactly the data Phase 2's GH loop guard
    needs and cannot reconstruct."""
    import inspect

    assert "status" not in inspect.signature(TicketService.update).parameters


# ------------------------------------------------------------ notifications


def test_the_assignee_is_notified_on_create(gateway, member):
    with as_admin(gateway):
        TicketService().create(a_bug(assignee_id=member), now=NOW)
    with as_user(gateway, member):
        assert unread_count(member) == 1
        assert inbox(member)[0].payload["key"] == "RL-1"


def test_assigning_yourself_notifies_nobody(gateway):
    with as_admin(gateway):
        TicketService().create(a_bug(assignee_id=gateway.admin_user_id), now=NOW)
        assert unread_count(gateway.admin_user_id) == 0


def test_reassignment_notifies_the_new_assignee(gateway, member, user_factory):
    other = user_factory(
        gateway.tenant_id, "second@zerosone.test", status=UserStatus.ACTIVE
    )
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(assignee_id=member), now=NOW)
        service.update(created.id, expected_rev=1, assignee_id=other)
    with as_user(gateway, other):
        assert unread_count(other) == 1


def test_a_status_change_notifies_assignee_and_reporter(gateway, member, user_factory):
    reporter = user_factory(
        gateway.tenant_id, "reporter@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    with as_user(gateway, reporter):
        created = TicketService().create(a_bug(assignee_id=member), now=NOW)
    # A third party moves it, so both the assignee and the reporter hear about it.
    with as_admin(gateway):
        TicketService().transition(created.id, S.IN_PROGRESS, expected_rev=1, now=NOW)

    with as_user(gateway, member):
        assert unread_count(member) == 2  # assignment + status change
    with as_user(gateway, reporter):
        assert unread_count(reporter) == 1


def test_moving_your_own_ticket_notifies_nobody_else(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(assignee_id=gateway.admin_user_id), now=NOW)
        service.transition(created.id, S.IN_PROGRESS, expected_rev=1, now=NOW)
        assert unread_count(gateway.admin_user_id) == 0


# -------------------------------------------------------------------- reads


def test_lookup_by_number_is_what_a_permalink_carries(gateway):
    with as_admin(gateway):
        created = TicketService().create(a_bug(), now=NOW)
        assert TicketService().by_number(created.number).id == created.id
        with pytest.raises(NotFound):
            TicketService().by_number(9999)


def test_a_guest_can_read_the_board(gateway, guest):
    """Tickets carry no share level, so they are tenant-wide — L3 by
    construction. Flagged as an open item: §5.4's "按分享级别" row is written
    for logs, and a per-ticket ACL would be a new column nobody has decided on.
    """
    with as_admin(gateway):
        TicketService().create(a_bug(), now=NOW)
    with as_user(gateway, guest):
        assert len(TicketService().list()) == 1


def test_the_list_filters(gateway, member):
    with as_admin(gateway):
        board = BoardMetadataService()
        label = board.create_label("网关")
        iteration = board.create_iteration("S1")
        service = TicketService()

        mine = service.create(a_bug(title="我的", assignee_id=member), now=NOW)
        tagged = service.create(a_bug(title="带标签", label_ids=(label.id,)), now=NOW)
        planned = service.create(
            a_bug(title="有迭代", iteration_id=iteration.id, priority=Priority.P0), now=NOW
        )
        service.transition(mine.id, S.IN_PROGRESS, expected_rev=1, now=NOW)

        listed = service.list
        assert {t.id for t in listed(TicketFilters(assignee_id=member))} == {mine.id}
        assert {t.id for t in listed(TicketFilters(label_id=label.id))} == {tagged.id}
        assert {t.id for t in listed(TicketFilters(iteration_id=iteration.id))} == {planned.id}
        assert {t.id for t in listed(TicketFilters(priority=(Priority.P0,)))} == {planned.id}
        assert {t.id for t in listed(TicketFilters(status=(S.IN_PROGRESS,)))} == {mine.id}
        assert {t.id for t in listed(TicketFilters(status=(S.TODO,)))} == {
            tagged.id,
            planned.id,
        }


def test_the_keyset_cursor_pages_without_repeating(gateway):
    """OFFSET both skips and repeats rows on a list ordered by ``updated_at``,
    which is exactly what a board is."""
    with as_admin(gateway):
        service = TicketService()
        for i in range(5):
            service.create(a_bug(title=f"第 {i}"), now=NOW)

        first_page = service.list(limit=2)
        assert len(first_page) == 2

        with tenant_session(context_for(gateway.tenant_id)) as session:
            last = session.get(Ticket, first_page[-1].id)
            cursor = (last.updated_at, last.id)

        second_page = service.list(TicketFilters(before=cursor), limit=2)

    assert {t.id for t in first_page} & {t.id for t in second_page} == set()


def test_another_tenants_tickets_are_not_listed(gateway):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        TicketService().create(a_bug(title="他们的"), now=NOW)
    with as_admin(gateway):
        assert TicketService().list() == []


# --------------------------------------------------------- TKT-8 · metadata


def test_a_label_is_created_and_renamed(gateway):
    with as_admin(gateway):
        board = BoardMetadataService()
        label = board.create_label("网关")
        assert label.color == "#6b7280"
        renamed = board.rename_label(label.id, name="网关团队", color="#F00")
        assert (renamed.name, renamed.color) == ("网关团队", "#f00")


def test_a_duplicate_label_name_is_refused(gateway):
    with as_admin(gateway):
        board = BoardMetadataService()
        board.create_label("网关")
        with pytest.raises(Conflict):
            board.create_label("  网关  ")


def test_a_bad_colour_is_refused(gateway):
    """The value is rendered into a style attribute, so this is injection
    surface rather than a cosmetic check."""
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            BoardMetadataService().create_label("网关", "red; content:'x'")


def test_a_member_may_open_an_iteration(gateway, member):
    """A Member running a project should not have to file a request to open next
    sprint. The destructive operation is the one that would need a role, and it
    does not exist."""
    with as_user(gateway, member):
        board = BoardMetadataService()
        iteration = board.create_iteration("S2", dt.date(2026, 9, 1), dt.date(2026, 9, 14))
        assert iteration.closed is False


def test_a_guest_may_not(gateway, guest):
    with as_user(gateway, guest):
        with pytest.raises(PermissionDenied):
            BoardMetadataService().create_label("试试")


def test_an_iteration_closes_and_reopens_without_touching_its_tickets(gateway):
    """Closing is a statement about the sprint, not about the work. Moving
    unfinished tickets is a decision somebody makes ticket by ticket."""
    with as_admin(gateway):
        board = BoardMetadataService()
        iteration = board.create_iteration("S1")
        ticket = TicketService().create(a_bug(iteration_id=iteration.id), now=NOW)

        assert board.set_iteration_closed(iteration.id, True).closed is True
        assert TicketService().get(ticket.id).iteration_id == iteration.id
        assert board.set_iteration_closed(iteration.id, False).closed is False

        assert [one.name for one in board.iterations(include_closed=False)] == ["S1"]


def test_backwards_iteration_dates_are_refused(gateway):
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            BoardMetadataService().create_iteration(
                "S3", dt.date(2026, 9, 14), dt.date(2026, 9, 1)
            )


def test_metadata_changes_are_audited(gateway):
    with as_admin(gateway):
        board = BoardMetadataService()
        label = board.create_label("网关")
        board.rename_label(label.id, name="网关团队")
        board.create_iteration("S1")
    actions = audit_actions(gateway.tenant_id)
    assert {"label.created", "label.updated", "iteration.created"} <= set(actions)


def test_history_is_readable_in_order(gateway):
    with as_admin(gateway):
        service = TicketService()
        created = service.create(a_bug(), now=NOW)
        service.transition(created.id, S.IN_PROGRESS, expected_rev=1, now=NOW)
        service.transition(created.id, S.IN_REVIEW, expected_rev=2, now=NOW)
        history = service.history(created.id)

    assert [row.to_status for row in history] == [S.TODO, S.IN_PROGRESS, S.IN_REVIEW]
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert len(session.scalars(select(TicketStatusHistory)).all()) == 3
