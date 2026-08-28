"""LOG-4 / LOG-6 / LOG-9 · authoring, versions, locks, sharing, retention.

The test worth reading first is
``test_the_list_query_agrees_with_the_rule_for_every_log``: the visible-logs SQL
is a second implementation of ``sharing.can_read``, and two implementations of
one access rule is exactly the kind of drift that produces a leak nobody can see
in a diff.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.spaces import SpaceService
from relay.app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from relay.app.logs.locks import LOCK_TTL, EditLockService
from relay.app.logs.retention import RETENTION, purge_old_versions
from relay.app.logs.service import KNOWLEDGE_MIN_BODY, LogService
from relay.app.logs.sharing import Reader, can_read
from relay.context import tenant_scope
from relay.domain.diffs import LineOp, line_diff
from relay.domain.enums import LogFormat, Role, ShareLevel, UserStatus
from relay.infra.db.models import AuditLog, Log, LogShareGrant, LogVersion, SpaceMember
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


def audit_actions(tenant_id):
    with tenant_session(context_for(tenant_id)) as session:
        return list(session.scalars(select(AuditLog.action)))


# ------------------------------------------------------------------ create


def test_a_log_starts_private_at_version_one(gateway, author):
    """Defaulting to L0 rather than L2: a draft that starts visible is a draft
    somebody reads mid-thought, and the cost of the wrong default runs one way."""
    with as_user(gateway, author):
        log = LogService().create("网关排查记录", "先记一句")
    assert log.share_level is ShareLevel.PRIVATE
    assert log.current_version == 1
    assert log.format is LogFormat.MARKDOWN
    assert "log.created" in audit_actions(gateway.tenant_id)


def test_a_blank_title_is_refused(gateway, author):
    with as_user(gateway, author):
        with pytest.raises(ValidationFailed):
            LogService().create("   ")


def test_a_guest_cannot_write_a_log(gateway, guest):
    with as_user(gateway, guest):
        with pytest.raises(PermissionDenied):
            LogService().create("试试")


def test_l2_without_a_space_is_refused(gateway, author):
    """A level that grants nothing, silently, is worse than an error."""
    with as_user(gateway, author):
        with pytest.raises(ValidationFailed):
            LogService().create("空间日志", share_level=ShareLevel.SPACE)


# ------------------------------------------------------- versions (LOG-4)


def test_each_distinct_save_appends_a_version(gateway, author):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "第一版")
        service.save(log.id, body="第二版")
        service.save(log.id, body="第三版")
        versions = service.versions(log.id)

    assert [v.version_no for v in versions] == [3, 2, 1]


def test_an_unchanged_autosave_writes_nothing(gateway, author):
    """Autosave fires on a timer, and most of those timers fire on text nobody
    touched. A version a second would bury the ones that mean something."""
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "内容")
        again = service.save(log.id, body="内容")
        assert again.current_version == 1
        assert len(service.versions(log.id)) == 1


def test_a_rollback_appends_rather_than_rewrites(gateway, author):
    """§6.2: history is never rewritten, so a rollback is itself reversible."""
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "第一版")
        service.save(log.id, body="第二版")
        rolled = service.rollback(log.id, 1)

        assert rolled.body == "第一版"
        assert rolled.current_version == 3
        versions = service.versions(log.id)

    assert [v.version_no for v in versions] == [3, 2, 1]
    assert versions[0].rolled_back_from == 1
    # Nothing was deleted or edited.
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert len(session.scalars(select(LogVersion)).all()) == 3


def test_rolling_back_to_a_missing_version_is_refused(gateway, author):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "内容")
        with pytest.raises(NotFound):
            service.rollback(log.id, 99)


def test_the_diff_is_by_line(gateway, author):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "第一行\n第二行\n第三行")
        service.save(log.id, body="第一行\n改过的第二行\n第三行")
        diff = service.diff(log.id, 1, 2)

    ops = [line.op for line in diff]
    assert LineOp.REMOVE in ops and LineOp.ADD in ops
    assert ops.count(LineOp.KEEP) == 2


def test_only_the_author_can_save(gateway, author, colleague):
    """§5.4 gives Member "编辑自己的"."""
    with as_user(gateway, author):
        log = LogService().create("排查", "内容", share_level=ShareLevel.TENANT)
    with as_user(gateway, colleague):
        with pytest.raises(PermissionDenied):
            LogService().save(log.id, body="我改一下")


def test_an_admin_can_save_somebody_elses_log(gateway, author):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
    with as_admin(gateway):
        assert LogService().save(log.id, body="管理员改的").current_version == 2


# --------------------------------------------------------- sharing (LOG-6)


def test_a_private_log_is_invisible_to_a_colleague(gateway, author, colleague):
    """Refused as ``NotFound``, not ``PermissionDenied``: a log you may not read
    is a log you should not learn exists. Same reasoning as MT-6's 404-not-403,
    applied inside a tenant."""
    with as_user(gateway, author):
        log = LogService().create("私密草稿", "还没写完")
    with as_user(gateway, colleague):
        with pytest.raises(NotFound):
            LogService().get(log.id)
        assert LogService().list() == []


def test_an_l1_grant_opens_it_to_exactly_one_person(gateway, author, colleague, user_factory):
    third = user_factory(gateway.tenant_id, "third@zerosone.test", status=UserStatus.ACTIVE)
    with as_user(gateway, author):
        service = LogService()
        log = service.create("给你看", "内容")
        service.set_share_level(log.id, ShareLevel.NAMED)
        service.grant(log.id, colleague)

    with as_user(gateway, colleague):
        assert LogService().get(log.id).title == "给你看"
    with as_user(gateway, third):
        with pytest.raises(NotFound):
            LogService().get(log.id)


def test_revoking_a_grant_closes_it_again(gateway, author, colleague):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("给你看", "内容")
        service.set_share_level(log.id, ShareLevel.NAMED)
        service.grant(log.id, colleague)
        service.revoke(log.id, colleague)
    with as_user(gateway, colleague):
        with pytest.raises(NotFound):
            LogService().get(log.id)


def test_granting_twice_is_idempotent(gateway, author, colleague):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("给你看", "内容")
        service.grant(log.id, colleague)
        service.grant(log.id, colleague)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert len(session.scalars(select(LogShareGrant)).all()) == 1


def test_an_l2_log_is_readable_by_the_space(gateway, author, colleague):
    with as_admin(gateway):
        space = SpaceService()
        space_id = space.create("平台组")
        space.add_member(space_id, author)
        space.add_member(space_id, colleague)

    with as_user(gateway, author):
        service = LogService()
        log = service.create("组内记录", "内容", space_id=space_id)
        service.set_share_level(log.id, ShareLevel.SPACE)

    with as_user(gateway, colleague):
        assert LogService().get(log.id).title == "组内记录"


def test_a_guest_in_the_space_cannot_read_an_l2_log(gateway, author, guest):
    """S-6 end to end, through the database this time."""
    with as_admin(gateway):
        space = SpaceService()
        space_id = space.create("平台组")
        space.add_member(space_id, author)
        space.add_member(space_id, guest)

    with as_user(gateway, author):
        service = LogService()
        log = service.create("组内记录", "内容", space_id=space_id)
        service.set_share_level(log.id, ShareLevel.SPACE)

    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.scalar(
            select(SpaceMember.id).where(SpaceMember.user_id == guest)
        ) is not None

    with as_user(gateway, guest):
        with pytest.raises(NotFound):
            LogService().get(log.id)
        assert LogService().list() == []


def test_an_l3_log_is_readable_by_a_guest(gateway, author, guest):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("全组织公告", "内容")
        service.set_share_level(log.id, ShareLevel.TENANT)
    with as_user(gateway, guest):
        assert LogService().get(log.id).title == "全组织公告"


def test_an_admin_reads_a_private_log(gateway, author):
    """§6.3: L0 is 仅作者 + Admin. Stated in a test because it is the kind of
    thing a reader assumes the other way."""
    with as_user(gateway, author):
        log = LogService().create("私密草稿", "还没写完")
    with as_admin(gateway):
        assert LogService().get(log.id).title == "私密草稿"
        assert len(LogService().list()) == 1


def test_a_share_level_change_is_audited(gateway, author):
    """The row somebody reads when asking how a document got out."""
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "内容")
        service.set_share_level(log.id, ShareLevel.TENANT)
    assert "log.share_level_changed" in audit_actions(gateway.tenant_id)


def test_another_tenants_log_is_invisible(gateway, author):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        theirs = LogService().create("他们的", "内容", share_level=ShareLevel.TENANT)
    with as_admin(gateway):
        # L3 in *their* tenant. MT decides this, not LOG (§6.3).
        with pytest.raises(NotFound):
            LogService().get(theirs.id)
        assert LogService().list() == []


def test_the_list_query_agrees_with_the_rule_for_every_log(
    gateway, author, colleague, guest
):
    """The one test that guards against two implementations of one rule.

    Builds a log at every share level, then checks the SQL predicate behind
    ``list()`` against the pure ``can_read`` for every (log, reader) pair. A
    divergence here is a leak or an invisible document, and neither shows up in
    a diff.
    """
    with as_admin(gateway):
        space = SpaceService()
        space_id = space.create("平台组")
        space.add_member(space_id, author)
        space.add_member(space_id, colleague)
        space.add_member(space_id, guest)

    with as_user(gateway, author):
        service = LogService()
        made = {}
        for level in ShareLevel:
            log = service.create(f"log-{level}", "内容", space_id=space_id)
            made[level] = service.set_share_level(log.id, level, space_id=space_id).id
        service.grant(made[ShareLevel.NAMED], colleague)

    readers = {
        "admin": (gateway.admin_user_id, Role.ADMIN),
        "author": (author, Role.MEMBER),
        "colleague": (colleague, Role.MEMBER),
        "guest": (guest, Role.GUEST),
    }

    for name, (user_id, role) in readers.items():
        with as_user(gateway, user_id):
            listed = {view.id for view in LogService().list(limit=100)}

        with tenant_session(context_for(gateway.tenant_id)) as session:
            for level, log_id in made.items():
                log = session.get(Log, log_id)
                has_grant = (
                    session.scalar(
                        select(LogShareGrant.id).where(
                            LogShareGrant.log_id == log_id,
                            LogShareGrant.user_id == user_id,
                        )
                    )
                    is not None
                )
                in_space = (
                    session.scalar(
                        select(SpaceMember.id).where(
                            SpaceMember.space_id == log.space_id,
                            SpaceMember.user_id == user_id,
                        )
                    )
                    is not None
                )
                expected = can_read(
                    share_level=level,
                    author_id=log.author_id,
                    reader=Reader(user_id=user_id, role=role),
                    has_named_grant=has_grant,
                    is_space_member=in_space,
                )
                assert (log_id in listed) is expected, (
                    f"{name} and level {level}: list() says {log_id in listed}, "
                    f"can_read says {expected}"
                )


# ------------------------------------------------------- the edit lock (S-7)


def test_the_lock_is_taken_and_renewed(gateway, author):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "内容")
        locks = EditLockService()
        held = locks.acquire(log.id, now=NOW)
        assert held.expires_at == NOW + LOCK_TTL
        assert held.taken_over_from is None

        later = NOW + dt.timedelta(minutes=2)
        assert locks.heartbeat(log.id, now=later).expires_at == later + LOCK_TTL


def test_reacquiring_your_own_live_lock_renews_it(gateway):
    """A browser that reloads mid-edit should not have to wait out its own TTL."""
    with as_admin(gateway):
        log = LogService().create("排查", "内容")
        locks = EditLockService()
        locks.acquire(log.id, now=NOW)
        later = NOW + dt.timedelta(minutes=1)
        renewed = locks.acquire(log.id, now=later)
    assert renewed.holder_id == gateway.admin_user_id
    assert renewed.expires_at == later + LOCK_TTL


def test_another_user_is_refused_while_the_lock_is_live(gateway, author):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        EditLockService().acquire(log.id, now=NOW)

    # An Admin may edit anyone's log, so they are the second editor here.
    with as_admin(gateway):
        with pytest.raises(Conflict) as refused:
            EditLockService().acquire(log.id, now=NOW + dt.timedelta(minutes=1))
    assert refused.value.detail["holder_id"] == str(author)


def test_once_the_ttl_lapses_the_lock_can_be_taken_over(gateway, author):
    """And the takeover reports the version the previous editor's work is in.

    That is what makes S-7's "unsaved content is saved as a version, never
    discarded" true: autosave already wrote it, so there is nothing to rescue
    and the message is a fact rather than a reassurance.
    """
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "第一版")
        EditLockService().acquire(log.id, now=NOW)
        service.save(log.id, body="自动保存的第二版")

    lapsed = NOW + LOCK_TTL + dt.timedelta(seconds=1)
    with as_admin(gateway):
        taken = EditLockService().acquire(log.id, now=lapsed)
    assert taken.taken_over_from == author
    assert taken.last_saved_version == 2


def test_a_heartbeat_after_the_ttl_is_refused(gateway, author):
    """Silently renewing a lapsed lock would mean a second editor could already
    be typing and the first would never be told."""
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        locks = EditLockService()
        locks.acquire(log.id, now=NOW)
        with pytest.raises(PermissionDenied):
            locks.heartbeat(log.id, now=NOW + LOCK_TTL + dt.timedelta(seconds=1))


def test_releasing_frees_it_immediately(gateway, author):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        locks = EditLockService()
        locks.acquire(log.id, now=NOW)
        assert locks.release(log.id, now=NOW) is True
        assert locks.holder(log.id, now=NOW) is None
    with as_admin(gateway):
        assert EditLockService().acquire(log.id, now=NOW).holder_id == gateway.admin_user_id


def test_a_lapsed_lock_reads_as_free(gateway, author):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        locks = EditLockService()
        locks.acquire(log.id, now=NOW)
        assert locks.holder(log.id, now=NOW) is not None
        assert locks.holder(log.id, now=NOW + LOCK_TTL) is None


# --------------------------------------------------------- import


def test_importing_a_markdown_file_creates_a_knowledge_log(gateway, author):
    with as_user(gateway, author):
        log = LogService().import_note(
            "限流.md", "# 网关限流\n\n当 QPS 超过 1000 时返回 429。".encode()
        )

    assert log.title == "网关限流"
    assert "QPS" in log.body
    assert log.format is LogFormat.MARKDOWN
    assert log.share_level is ShareLevel.PRIVATE
    assert log.knowledge_candidate is True
    assert log.marked_by == author
    assert "log.imported" in audit_actions(gateway.tenant_id)


def test_importing_html_stores_markdown_not_markup(gateway, author):
    html = (
        "<html><head><title>对照</title></head>"
        "<body><p><strong>限流</strong></p></body></html>"
    ).encode()
    with as_user(gateway, author):
        log = LogService().import_note("table.html", html)
    assert log.title == "对照"
    assert "**限流**" in log.body
    assert "<strong>" not in log.body


def test_a_guest_cannot_import_a_note(gateway, guest):
    with as_user(gateway, guest):
        with pytest.raises(PermissionDenied):
            LogService().import_note("x.md", b"# Hi\n")


def test_an_unsupported_import_is_refused(gateway, author):
    with as_user(gateway, author):
        with pytest.raises(ValidationFailed):
            LogService().import_note("notes.pdf", b"%PDF")


# --------------------------------------------------------- LOG-9 · knowledge


def test_marking_records_who_and_when(gateway, author):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "内容")
        marked = service.mark_knowledge_candidate(log.id, now=NOW)

    assert marked.knowledge_candidate is True
    assert marked.marked_by == author
    assert marked.marked_at == NOW
    assert "log.knowledge_marked" in audit_actions(gateway.tenant_id)


def test_unmarking_clears_the_provenance(gateway, author):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "内容")
        service.mark_knowledge_candidate(log.id, now=NOW)
        cleared = service.mark_knowledge_candidate(log.id, False, now=NOW)
    assert cleared.marked_by is None and cleared.marked_at is None


def test_the_counting_rule_needs_both_halves(gateway, author):
    """S-16: **checked and body ≥ 300 characters.**

    The checkbox alone would count a one-line note somebody ticked out of
    optimism; the length alone would count every long log nobody judged.
    """
    with as_user(gateway, author):
        service = LogService()
        short_marked = service.create("短的", "x" * (KNOWLEDGE_MIN_BODY - 1))
        long_unmarked = service.create("长但没勾", "x" * KNOWLEDGE_MIN_BODY)
        long_marked = service.create("长且勾了", "x" * KNOWLEDGE_MIN_BODY)
        service.mark_knowledge_candidate(short_marked.id, now=NOW)
        service.mark_knowledge_candidate(long_marked.id, now=NOW)
        assert long_unmarked.knowledge_candidate is False

        assert service.knowledge_candidate_count() == 1


# ------------------------------------------------------ retention (S-8)


def test_old_versions_are_purged_but_the_latest_is_kept(gateway, author):
    """The half that keeps the decision safe. Without it, a log nobody touched
    for three months would lose the only copy of its current text."""
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "第一版")
        service.save(log.id, body="第二版")
        service.save(log.id, body="第三版")

    # Age every version past the window. Through the BYPASSRLS connection, not
    # the owner's: FORCE ROW LEVEL SECURITY binds relay_owner too, so the
    # policy's current_setting('app.tenant_id') would raise for it as well.
    from sqlalchemy import text

    from relay.infra.db.engine import system_engine

    with system_engine().begin() as conn:
        conn.execute(text("UPDATE log_version SET created_at = now() - interval '200 days'"))

    with as_admin(gateway):
        removed = purge_old_versions(now=dt.datetime.now(dt.UTC))

    assert removed == 2
    with tenant_session(context_for(gateway.tenant_id)) as session:
        remaining = session.scalars(select(LogVersion)).all()
    assert [row.version_no for row in remaining] == [3]


def test_recent_versions_survive(gateway, author):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "第一版")
        service.save(log.id, body="第二版")
    with as_admin(gateway):
        assert purge_old_versions(now=dt.datetime.now(dt.UTC)) == 0


def test_a_member_cannot_run_the_purge(gateway, author):
    """A destructive maintenance operation. Being normally invoked by a
    scheduler is not a reason to let any session trigger it."""
    with as_user(gateway, author):
        with pytest.raises(PermissionDenied):
            purge_old_versions(now=dt.datetime.now(dt.UTC))


def test_the_retention_window_is_ninety_days():
    assert RETENTION == dt.timedelta(days=90)


# ------------------------------------------------------------- the diff util


def test_the_diff_numbers_both_sides():
    diff = line_diff("a\nb\nc", "a\nB\nc")
    kept = [line for line in diff if line.op is LineOp.KEEP]
    assert [(line.old_no, line.new_no) for line in kept] == [(1, 1), (3, 3)]


def test_an_empty_document_diffs_cleanly():
    assert all(line.op is LineOp.ADD for line in line_diff("", "新内容"))
    assert all(line.op is LineOp.REMOVE for line in line_diff("旧内容", ""))


def test_an_identical_document_has_no_changes():
    assert all(line.op is LineOp.KEEP for line in line_diff("a\nb", "a\nb"))
