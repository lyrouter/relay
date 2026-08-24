"""TKT-4 · comments, @mentions, and the rule that the API is not silent.

Most of these tests are about *not* notifying: the parser half of a mention
system is judged by what it refuses to match, because the cost of a false
positive is pinging a colleague who has nothing to do with the ticket.
"""

from __future__ import annotations

import datetime as dt

import pytest

from relay.app.accounts.administration import AdminService
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.errors import NotFound, PermissionDenied, ValidationFailed
from relay.app.notifications import inbox, unread_count
from relay.app.tickets.comments import CommentService
from relay.app.tickets.service import NewTicket, TicketService
from relay.context import ActorType, Origin, TenantContext, tenant_scope
from relay.domain.enums import NotificationType, Role, TicketType, UserStatus
from relay.domain.mentions import MAX_MENTIONS, parse
from relay.infra.db.models import TicketComment
from relay.infra.db.session import tenant_session

from .conftest import context_for, requires_db

PASSWORD = "Corr3ct-Horse-Battery"
NOW = dt.datetime(2026, 8, 24, 10, 0, tzinfo=dt.UTC)


# ------------------------------------------------------- the parser (no db)


def test_a_plain_mention_is_found():
    assert parse("@lisa 看一下") == ("lisa",)


def test_mentions_are_distinct_and_in_order():
    assert parse("@bob @lisa @bob") == ("bob", "lisa")


def test_an_email_address_is_not_a_mention():
    """"ping bob@zerosone.test" must not mention ``zerosone.test`` — the GH-sync
    principle arriving early: never @ an unrelated account."""
    assert parse("ping bob@zerosone.test") == ()


def test_a_fenced_code_block_does_not_mention_anyone():
    body = "看日志：\n```\nERROR at @lisa handler\n```\n"
    assert parse(body) == ()


def test_an_inline_code_span_does_not_either():
    assert parse("变量叫 `@lisa`，不是提及") == ()


def test_a_mention_outside_the_fence_still_counts():
    assert parse("```\n@bob\n```\n@lisa 看一下") == ("lisa",)


def test_trailing_punctuation_is_not_part_of_the_handle():
    assert parse("@lisa, @bob. @dev-team-") == ("lisa", "bob", "dev-team")


def test_case_is_normalised():
    assert parse("@Lisa") == ("lisa",)


def test_a_bare_at_is_not_a_mention():
    assert parse("@ @@ @!") == ()


# ------------------------------------------------------------ the use case


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
def lisa(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "lisa@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )


@pytest.fixture
def ticket(gateway):
    with tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id)):
        return TicketService().create(
            NewTicket(type=TicketType.BUG, title="网关 502"), now=NOW
        )


def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


def as_user(gateway, user_id):
    return tenant_scope(context_for(gateway.tenant_id, user_id))


@requires_db
@pytest.mark.db
def test_a_comment_is_stored(gateway, ticket):
    with as_admin(gateway):
        view = CommentService().add(ticket.id, "  已复现  ", now=NOW)
    assert view.body == "已复现"
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.get(TicketComment, view.id).ticket_id == ticket.id


@requires_db
@pytest.mark.db
def test_an_empty_comment_is_refused(gateway, ticket):
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            CommentService().add(ticket.id, "   ", now=NOW)


@requires_db
@pytest.mark.db
def test_a_guest_cannot_comment(gateway, ticket, user_factory):
    guest = user_factory(
        gateway.tenant_id, "guest@zerosone.test", role=Role.GUEST, status=UserStatus.ACTIVE
    )
    with as_user(gateway, guest):
        with pytest.raises(PermissionDenied):
            CommentService().add(ticket.id, "我也说一句", now=NOW)


@requires_db
@pytest.mark.db
def test_another_tenants_ticket_cannot_be_commented_on(gateway):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        theirs = TicketService().create(
            NewTicket(type=TicketType.TASK, title="他们的"), now=NOW
        )
    with as_admin(gateway):
        with pytest.raises(NotFound):
            CommentService().add(theirs.id, "不该评论到", now=NOW)


@requires_db
@pytest.mark.db
def test_a_mention_notifies_the_person(gateway, ticket, lisa):
    with as_admin(gateway):
        view = CommentService().add(ticket.id, "@lisa 麻烦看一下", now=NOW)
    assert view.mentioned == (lisa,)

    with as_user(gateway, lisa):
        assert unread_count(lisa) == 1
        item = inbox(lisa)[0]
    assert item.type is NotificationType.MENTION
    assert item.payload["key"] == ticket.key


@requires_db
@pytest.mark.db
def test_a_handle_matching_nobody_stays_plain_text(gateway, ticket):
    """Not an error, and not a notification. The comment keeps what was
    written — the alternative is refusing a comment because of a typo."""
    with as_admin(gateway):
        view = CommentService().add(ticket.id, "@nobody-here 看一下", now=NOW)
    assert view.mentioned == ()
    assert "@nobody-here" in view.body


@requires_db
@pytest.mark.db
def test_mentioning_yourself_notifies_nobody(gateway, ticket):
    with as_admin(gateway):
        view = CommentService().add(ticket.id, "@admin 自己记一笔", now=NOW)
        assert view.mentioned == ()
        assert unread_count(gateway.admin_user_id) == 0


@requires_db
@pytest.mark.db
def test_a_deactivated_colleague_is_skipped_silently(gateway, ticket, lisa):
    """R-2 deactivates departing accounts; they should not keep accumulating
    notifications, and the comment should not fail because someone left."""
    with as_admin(gateway):
        AdminService().deactivate_user(lisa)
        view = CommentService().add(ticket.id, "@lisa 还在吗", now=NOW)
    assert view.mentioned == ()


@requires_db
@pytest.mark.db
def test_a_mention_in_another_tenant_matches_nobody(gateway, ticket):
    """The local part is unique *within* a tenant (AC-9's one-to-one domain), and
    resolution runs under RLS — so `@admin` cannot reach another tenant's admin
    even though the handle is identical."""
    bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with as_admin(gateway):
        # Resolves to this tenant's admin — who is the actor, so nobody is
        # notified — and never to the other tenant's.
        assert CommentService().add(ticket.id, "@admin", now=NOW).mentioned == ()


@requires_db
@pytest.mark.db
def test_repeated_mentions_in_a_thread_fold_into_one_unread(gateway, ticket, lisa):
    """NT-2. Being mentioned four times in one thread is one thing to come and
    read, which is why the aggregation target is the ticket and not the comment.
    """
    with as_admin(gateway):
        service = CommentService()
        for i in range(4):
            service.add(ticket.id, f"@lisa 第 {i} 次", now=NOW)
    with as_user(gateway, lisa):
        assert unread_count(lisa) == 1
        assert inbox(lisa)[0].folded_count == 4


@requires_db
@pytest.mark.db
def test_too_many_mentions_is_refused(gateway, ticket):
    """A comment mentioning the whole company is a broadcast, and S1 has no
    broadcast feature. The cap is where that gets noticed."""
    body = " ".join(f"@person{i}" for i in range(MAX_MENTIONS + 1))
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            CommentService().add(ticket.id, body, now=NOW)


@requires_db
@pytest.mark.db
def test_a_comment_through_the_api_still_notifies(gateway, ticket, lisa):
    """§7.5: otherwise the API is a back door for editing tickets that nobody is
    told about. Same use case, different origin — and the row records which."""
    ctx = TenantContext(
        tenant_id=gateway.tenant_id,
        actor_id=gateway.admin_user_id,
        actor_type=ActorType.INTEGRATION,
        origin=Origin.API,
    )
    with tenant_scope(ctx):
        view = CommentService().add(ticket.id, "@lisa 自动化留言", now=NOW)
    assert view.mentioned == (lisa,)

    with tenant_session(context_for(gateway.tenant_id)) as session:
        row = session.get(TicketComment, view.id)
    assert row.actor_type is ActorType.INTEGRATION
    assert row.origin is Origin.API

    with as_user(gateway, lisa):
        assert unread_count(lisa) == 1


@requires_db
@pytest.mark.db
def test_comments_are_listed_oldest_first(gateway, ticket):
    with as_admin(gateway):
        service = CommentService()
        service.add(ticket.id, "第一条", now=NOW)
        service.add(ticket.id, "第二条", now=NOW)
        assert [one.body for one in service.list(ticket.id)] == ["第一条", "第二条"]


# --------------------------------------------------- S-21 · Guests and mentions


@pytest.fixture
def contractor(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "vendor@zerosone.test", role=Role.GUEST, status=UserStatus.ACTIVE
    )


@requires_db
@pytest.mark.db
def test_a_guest_cannot_read_the_comments_on_someone_elses_ticket(
    gateway, ticket, contractor
):
    """S-21 applies to the thread as well as to the ticket.

    A comment list that stayed readable would hand over exactly what the
    decision withholds — who is working on what — through a different endpoint.
    """
    with as_admin(gateway):
        CommentService().add(ticket.id, "内部讨论", now=NOW)
    with as_user(gateway, contractor):
        with pytest.raises(NotFound):
            CommentService().list(ticket.id)


@requires_db
@pytest.mark.db
def test_a_guest_reads_the_thread_on_their_own_ticket(gateway, contractor):
    with as_admin(gateway):
        assigned = TicketService().create(
            NewTicket(type=TicketType.BUG, title="外部任务", assignee_id=contractor), now=NOW
        )
        CommentService().add(assigned.id, "细节见附件", now=NOW)
    with as_user(gateway, contractor):
        assert [one.body for one in CommentService().list(assigned.id)] == ["细节见附件"]


@requires_db
@pytest.mark.db
def test_mentioning_a_guest_who_cannot_see_the_ticket_notifies_nobody(
    gateway, ticket, contractor
):
    """The mention stays text (S-21).

    Notifying would be worse than dropping it: the inbox would say "you were
    mentioned on RL-1", the link would 404, and the Guest would have learned the
    ticket exists — while the author believed the ping landed.
    """
    with as_admin(gateway):
        view = CommentService().add(ticket.id, "@vendor 你看下", now=NOW)
    assert view.mentioned == ()
    with as_user(gateway, contractor):
        assert unread_count(contractor) == 0


@requires_db
@pytest.mark.db
def test_mentioning_a_guest_on_their_own_ticket_does_notify(gateway, contractor):
    """The other half — otherwise "drop mentions of Guests" would be the rule,
    and the assignee of a ticket could not be pinged on it."""
    with as_admin(gateway):
        assigned = TicketService().create(
            NewTicket(type=TicketType.BUG, title="外部任务", assignee_id=contractor), now=NOW
        )
        view = CommentService().add(assigned.id, "@vendor 有更新", now=NOW)
    assert view.mentioned == (contractor,)
    with as_user(gateway, contractor):
        # The assignment notification plus the mention.
        assert unread_count(contractor) == 2
