"""LOG-8 · full-text search over pgroonga.

Two properties carry this file. Chinese text has to actually match — that is the
whole reason F-2 chased pgroonga rather than settling for PostgreSQL's default
tokeniser — and **search must not return a log the searcher cannot read.** The
second is the one that would be a leak: RLS gets the tenant right by itself and
knows nothing about share levels, so a match on somebody's private draft would
sail straight through.
"""

from __future__ import annotations

import pytest

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.spaces import SpaceService
from relay.app.logs.service import LogService
from relay.app.tickets.service import NewTicket, TicketService
from relay.context import tenant_scope
from relay.domain.enums import Role, ShareLevel, TicketType, UserStatus
from relay.infra.search import PgroongaSearch

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"


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
        gateway.tenant_id, "author@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )


@pytest.fixture
def colleague(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "colleague@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )


@pytest.fixture
def guest(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "contractor@zerosone.test", role=Role.GUEST, status=UserStatus.ACTIVE
    )


def as_user(gateway, user_id):
    return tenant_scope(context_for(gateway.tenant_id, user_id))


def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


def titles(results):
    return {hit.title for hit in results.hits}


# ------------------------------------------------------------------ matching


def test_a_chinese_term_matches_a_body(gateway, author):
    """The reason F-2 chased pgroonga: PostgreSQL's default tokeniser does not
    segment Chinese, so this is the test that would fail without it."""
    with as_user(gateway, author):
        LogService().create(
            "网关排查记录", "上游连接超时，重试三次后仍然失败", share_level=ShareLevel.TENANT
        )
        results = PgroongaSearch().search("超时")

    assert titles(results) == {"网关排查记录"}


def test_a_title_matches_too(gateway, author):
    with as_user(gateway, author):
        LogService().create("模型路由说明", "按权重分流", share_level=ShareLevel.TENANT)
        assert titles(PgroongaSearch().search("路由")) == {"模型路由说明"}


def test_latin_text_matches(gateway, author):
    with as_user(gateway, author):
        LogService().create(
            "gateway timeout notes", "upstream connection reset", share_level=ShareLevel.TENANT
        )
        assert titles(PgroongaSearch().search("upstream")) == {"gateway timeout notes"}


def test_a_term_that_matches_nothing_returns_nothing(gateway, author):
    with as_user(gateway, author):
        LogService().create("网关排查", "内容", share_level=ShareLevel.TENANT)
        assert PgroongaSearch().search("完全不相关的词").hits == ()


def test_an_empty_query_returns_nothing_rather_than_everything(gateway, author):
    """A search box that returns the corpus on an accidental Enter is how people
    find things they were not looking for."""
    with as_user(gateway, author):
        LogService().create("网关排查", "内容", share_level=ShareLevel.TENANT)
        assert PgroongaSearch().search("   ").hits == ()


def test_pgroonga_query_syntax_is_not_exposed(gateway, author):
    """``&@`` rather than ``&@~``: a stray bracket from a user must not become
    either an error or a silently different search."""
    with as_user(gateway, author):
        LogService().create("网关排查", "内容", share_level=ShareLevel.TENANT)
        # Would be a valid pgroonga OR-query under &@~; here it is just terms.
        assert PgroongaSearch().search("((网关 OR 别的").hits == ()


# ------------------------------------------------------- tickets (§6.4)


def test_a_ticket_title_is_searchable(gateway, author):
    with as_user(gateway, author):
        TicketService().create(NewTicket(type=TicketType.BUG, title="网关返回 502"))
        results = PgroongaSearch().search("网关")
    assert {hit.kind for hit in results.hits} == {"ticket"}
    assert titles(results) == {"网关返回 502"}


def test_a_ticket_description_is_not_indexed(gateway, author):
    """§6.4 lists titles for tickets. Descriptions are largely stack traces and
    pasted logs, so indexing them would double the index for the worst signal."""
    with as_user(gateway, author):
        TicketService().create(
            NewTicket(
                type=TicketType.BUG,
                title="一个工单",
                description="这里写了超时两个字",
            )
        )
        assert PgroongaSearch().search("超时").hits == ()


def test_both_kinds_come_back_together(gateway, author):
    with as_user(gateway, author):
        LogService().create("网关排查记录", "内容", share_level=ShareLevel.TENANT)
        TicketService().create(NewTicket(type=TicketType.BUG, title="网关返回 502"))
        results = PgroongaSearch().search("网关")
    assert {hit.kind for hit in results.hits} == {"log", "ticket"}


def test_the_kind_filter_narrows_it(gateway, author):
    with as_user(gateway, author):
        LogService().create("网关排查记录", "内容", share_level=ShareLevel.TENANT)
        TicketService().create(NewTicket(type=TicketType.BUG, title="网关返回 502"))
        only_logs = PgroongaSearch().search("网关", kinds=("log",))
    assert {hit.kind for hit in only_logs.hits} == {"log"}


# ---------------------------------------------- share levels (the leak test)


def test_search_does_not_return_a_private_log_to_a_colleague(gateway, author, colleague):
    """**The leak this file exists for.** RLS gets the tenant right and knows
    nothing about share levels, so without the visibility clause pgroonga would
    happily match a colleague's private draft."""
    with as_user(gateway, author):
        LogService().create("私密排查", "上游连接超时")
    with as_user(gateway, colleague):
        assert PgroongaSearch().search("超时").hits == ()


def test_the_author_finds_their_own_private_log(gateway, author):
    with as_user(gateway, author):
        LogService().create("私密排查", "上游连接超时")
        assert titles(PgroongaSearch().search("超时")) == {"私密排查"}


def test_an_admin_finds_a_private_log(gateway, author):
    """Consistent with §6.3 — an Admin reads L0, so search must agree with the
    read path rather than being quietly stricter."""
    with as_user(gateway, author):
        LogService().create("私密排查", "上游连接超时")
    with as_admin(gateway):
        assert titles(PgroongaSearch().search("超时")) == {"私密排查"}


def test_an_l1_grant_makes_it_findable(gateway, author, colleague):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("给你看", "上游连接超时")
        service.set_share_level(log.id, ShareLevel.NAMED)
        service.grant(log.id, colleague)
    with as_user(gateway, colleague):
        assert titles(PgroongaSearch().search("超时")) == {"给你看"}


def test_a_guest_in_the_space_cannot_find_an_l2_log(gateway, author, guest):
    """S-6 reaching the search path too. A rule enforced on read and forgotten
    on search is not enforced."""
    with as_admin(gateway):
        space = SpaceService()
        space_id = space.create("平台组")
        space.add_member(space_id, author)
        space.add_member(space_id, guest)

    with as_user(gateway, author):
        service = LogService()
        log = service.create("组内记录", "上游连接超时", space_id=space_id)
        service.set_share_level(log.id, ShareLevel.SPACE, space_id=space_id)

    with as_user(gateway, guest):
        assert PgroongaSearch().search("超时").hits == ()


def test_a_member_in_the_space_can(gateway, author, colleague):
    with as_admin(gateway):
        space = SpaceService()
        space_id = space.create("平台组")
        space.add_member(space_id, author)
        space.add_member(space_id, colleague)

    with as_user(gateway, author):
        service = LogService()
        log = service.create("组内记录", "上游连接超时", space_id=space_id)
        service.set_share_level(log.id, ShareLevel.SPACE, space_id=space_id)

    with as_user(gateway, colleague):
        assert titles(PgroongaSearch().search("超时")) == {"组内记录"}


def test_another_tenants_content_is_never_returned(gateway, author):
    """MT's guarantee, pinned on the search path because an index is exactly the
    kind of thing that gets built once and queried from everywhere."""
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        LogService().create("他们的排查", "上游连接超时", share_level=ShareLevel.TENANT)
        TicketService().create(NewTicket(type=TicketType.BUG, title="他们的超时工单"))

    with as_user(gateway, author):
        assert PgroongaSearch().search("超时").hits == ()


# ---------------------------------------------------------------- snippets


def test_the_snippet_shows_the_match_in_context(gateway, author):
    body = "前面很多字。" * 40 + "关键的超时信息在这里。" + "后面还有很多字。" * 40
    with as_user(gateway, author):
        LogService().create("长日志", body, share_level=ShareLevel.TENANT)
        hit = PgroongaSearch().search("超时").hits[0]
    assert len(hit.snippet) < len(body)
    assert hit.snippet.endswith("…") or hit.snippet.startswith("…")


def test_an_empty_body_gets_an_empty_snippet(gateway, author):
    with as_user(gateway, author):
        LogService().create("只有标题的网关记录", "", share_level=ShareLevel.TENANT)
        assert PgroongaSearch().search("网关").hits[0].snippet == ""


def test_indexing_is_a_no_op_because_postgres_does_it(gateway, author):
    """The methods exist because the port declares them, and because an external
    engine would need exactly these call sites."""
    with as_user(gateway, author):
        log = LogService().create("网关排查", "内容", share_level=ShareLevel.TENANT)
        search = PgroongaSearch()
        assert search.index_log(log.id) is None
        assert titles(search.search("网关")) == {"网关排查"}
