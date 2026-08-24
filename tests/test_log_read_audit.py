"""S-19 · an Admin reads every share level, and the read leaves a trail.

The decision has two halves and this file is the second one. The first half —
that an Admin *may* read a colleague's private log — is pinned in
``test_log_sharing.py``; here the question is what ends up in ``audit_log``, and
the properties worth spending tests on are the ones that make the trail readable:

* the privileged read is recorded, from every surface that returns content;
* an ordinary read is **not** recorded, by anyone, ever — including an Admin's
  own logs, tenant-wide logs, and logs they were explicitly granted;
* a list or a search writes **one** row naming what it surfaced, not one per row.

The last one is the difference between a trail somebody reads and a table nobody
opens.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid

import pytest
from sqlalchemy import select

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.spaces import SpaceService
from relay.app.errors import NotFound
from relay.app.logs.attachments import AttachmentService
from relay.app.logs.read_audit import ACTION
from relay.app.logs.service import LogService
from relay.app.search import SearchUseCase
from relay.context import tenant_scope
from relay.domain.enums import Role, ShareLevel, SpaceRole, UserStatus
from relay.infra.blob.filesystem import FilesystemBlobStore
from relay.infra.db.models import AuditLog
from relay.infra.db.session import tenant_session
from relay.infra.search import PgroongaSearch

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


@pytest.fixture
def colleague(gateway, user_factory):
    return user_factory(
        gateway.tenant_id, "bob@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )


def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


def as_user(gateway, user_id):
    return tenant_scope(context_for(gateway.tenant_id, user_id))


def reads(tenant_id) -> list[AuditLog]:
    with tenant_session(context_for(tenant_id)) as session:
        return list(
            session.scalars(select(AuditLog).where(AuditLog.action == ACTION))
        )


def a_space_the_admin_is_not_in(gateway, author) -> uuid.UUID:
    """Only an Admin may create a space (SPACE_MANAGE), and the creator becomes
    its first owner — so getting a space the Admin is *not* in takes handing
    ownership over and stepping out. Worth the three lines: the interesting L2
    case is the one where membership is not what let them read."""
    with as_admin(gateway):
        service = SpaceService()
        space_id = service.create("网关组")
        service.add_member(space_id, author, SpaceRole.OWNER)
        service.remove_member(space_id, gateway.admin_user_id)
    return space_id


def a_private_log(gateway, author, title="排查记录"):
    with as_user(gateway, author):
        return LogService().create(title, "私密草稿" * 30, now=NOW)


# ------------------------------------------------------------- it is recorded


def test_an_admin_reading_a_private_log_is_recorded(gateway, author):
    """The row is the whole point of S-19: without it, "the Admin can read
    everything" is a permission nobody can review after the fact."""
    log = a_private_log(gateway, author)
    with as_admin(gateway):
        assert LogService().get(log.id).title == "排查记录"

    rows = reads(gateway.tenant_id)
    assert len(rows) == 1
    assert rows[0].target_id == str(log.id)
    assert rows[0].actor_id == gateway.admin_user_id
    assert rows[0].after["via"] == "get"
    assert rows[0].after["share_level"] == str(ShareLevel.PRIVATE)
    assert rows[0].after["author_id"] == str(author)


@pytest.mark.parametrize("level", [ShareLevel.PRIVATE, ShareLevel.NAMED, ShareLevel.SPACE])
def test_every_level_an_ordinary_member_could_not_reach_is_recorded(
    gateway, author, level
):
    """L0, L1 with no grant, and L2 with no membership — all three are reads the
    role made possible, so all three leave a row. L3 is the exception and has
    its own test."""
    log = a_private_log(gateway, author)
    space_id = (
        a_space_the_admin_is_not_in(gateway, author) if level is ShareLevel.SPACE else None
    )
    with as_user(gateway, author):
        LogService().set_share_level(log.id, level, space_id=space_id)

    with as_admin(gateway):
        LogService().get(log.id)
    assert len(reads(gateway.tenant_id)) == 1


def test_versions_diff_and_grantees_are_reads_too(gateway, author):
    """Content does not only leave through ``get``. A version list plus a diff
    reconstructs the document, so a trail that covered only the detail page
    would be a trail with a hole in the shape of the history tab."""
    log = a_private_log(gateway, author)
    with as_user(gateway, author):
        LogService().save(log.id, body="第二版" * 30, now=NOW)

    with as_admin(gateway):
        service = LogService()
        service.versions(log.id)
        service.diff(log.id, 1, 2)
        service.grantees(log.id)

    assert sorted(row.after["via"] for row in reads(gateway.tenant_id)) == [
        "diff",
        "grantees",
        "versions",
    ]


def test_downloading_an_attachment_off_a_private_log_is_recorded(gateway, author, tmp_path):
    """S-11 makes the download a permission-checked act; S-19 makes it a recorded
    one. The file *is* the content."""
    store = FilesystemBlobStore(root=str(tmp_path))
    log = a_private_log(gateway, author)
    with as_user(gateway, author):
        uploaded = AttachmentService(store).upload(
            "log", log.id, "trace.txt", "text/plain", io.BytesIO(b"stack trace")
        )

    with as_admin(gateway):
        AttachmentService(store).link(uploaded.id)

    rows = reads(gateway.tenant_id)
    assert [row.after["via"] for row in rows] == ["attachment"]


# --------------------------------------------------------- it is not recorded


def test_reading_your_own_log_records_nothing(gateway, author):
    log = a_private_log(gateway, author)
    with as_user(gateway, author):
        LogService().get(log.id)
    assert reads(gateway.tenant_id) == []


def test_an_admin_reading_their_own_log_records_nothing(gateway):
    with as_admin(gateway):
        mine = LogService().create("我的草稿", "内容" * 200, now=NOW)
        LogService().get(mine.id)
    assert reads(gateway.tenant_id) == []


def test_a_tenant_wide_log_records_nothing(gateway, author):
    """L3 is a read any Member could make, so the Admin power was not used.

    This is the test that keeps the trail worth reading: without it, every Admin
    who opens the team's shared notes files a privileged-read row, and the
    twenty rows that matter are lost among ten thousand.
    """
    log = a_private_log(gateway, author)
    with as_user(gateway, author):
        LogService().set_share_level(log.id, ShareLevel.TENANT)
    with as_admin(gateway):
        LogService().get(log.id)
    assert reads(gateway.tenant_id) == []


def test_an_admin_who_was_granted_the_log_records_nothing(gateway, author):
    """They were **named** on it. The role was not what let them in, and a row
    saying otherwise would misrepresent an ordinary shared document as a
    privileged read."""
    log = a_private_log(gateway, author)
    with as_user(gateway, author):
        LogService().set_share_level(log.id, ShareLevel.NAMED)
        LogService().grant(log.id, gateway.admin_user_id)
    with as_admin(gateway):
        LogService().get(log.id)
    assert reads(gateway.tenant_id) == []


def test_an_admin_in_the_space_records_nothing(gateway, author):
    """Same reasoning one level up: any Member of that space could have read it.

    The Admin is a member here because they created the space, which is the
    common case — and the one a coarse "Admin read a non-L3 log" rule would
    report as privileged."""
    with as_admin(gateway):
        space_id = SpaceService().create("网关组")
    log = a_private_log(gateway, author)
    with as_user(gateway, author):
        LogService().set_share_level(log.id, ShareLevel.SPACE, space_id=space_id)
    with as_admin(gateway):
        LogService().get(log.id)
    assert reads(gateway.tenant_id) == []


def test_a_member_reading_a_shared_log_records_nothing(gateway, author, colleague):
    """Ordinary browsing is never recorded, from any role but Admin and never
    for a read anyone could make."""
    log = a_private_log(gateway, author)
    with as_user(gateway, author):
        LogService().set_share_level(log.id, ShareLevel.NAMED)
        LogService().grant(log.id, colleague)
    with as_user(gateway, colleague):
        LogService().get(log.id)
        LogService().list()
    assert reads(gateway.tenant_id) == []


def test_a_refused_read_records_nothing(gateway, author, colleague):
    """A read that raised ``NotFound`` did not happen, so there is nothing to
    record — and a row would tell an investigator the opposite."""
    log = a_private_log(gateway, author)
    with as_user(gateway, colleague):
        with pytest.raises(NotFound):
            LogService().get(log.id)
    assert reads(gateway.tenant_id) == []


# ------------------------------------------------- one row per call, not per row


def test_a_list_writes_one_row_naming_what_it_surfaced(gateway, author):
    log_ids = []
    with as_user(gateway, author):
        for i in range(3):
            log_ids.append(LogService().create(f"草稿 {i}", "内容" * 200, now=NOW).id)
    with as_admin(gateway):
        assert len(LogService().list()) == 3

    rows = reads(gateway.tenant_id)
    assert len(rows) == 1
    assert rows[0].target_id is None
    assert rows[0].after["count"] == 3
    assert set(rows[0].after["log_ids"]) == {str(one) for one in log_ids}
    assert rows[0].after["via"] == "list"


def test_a_list_that_surfaced_nothing_privileged_writes_nothing(gateway, author):
    """An Admin listing their own logs plus the team's L3 notes is browsing."""
    with as_user(gateway, author):
        shared = LogService().create("团队笔记", "内容" * 200, now=NOW)
        LogService().set_share_level(shared.id, ShareLevel.TENANT)
    with as_admin(gateway):
        LogService().create("我的", "内容" * 200, now=NOW)
        assert len(LogService().list()) == 2
    assert reads(gateway.tenant_id) == []


def test_a_search_hit_on_a_private_log_is_recorded_once(gateway, author):
    """A snippet is content. Recorded in the use case rather than in the
    pgroonga adapter, so ``SearchPort`` stays implementable by anything that can
    match text."""
    with as_user(gateway, author):
        LogService().create("超时排查", "网关 upstream 超时" * 20, now=NOW)
        LogService().create("另一份超时记录", "还是超时" * 20, now=NOW)

    with as_admin(gateway):
        results = SearchUseCase(PgroongaSearch()).execute("超时")
        assert len(results.hits) == 2

    rows = reads(gateway.tenant_id)
    assert len(rows) == 1
    assert rows[0].after["via"] == "search"
    assert rows[0].after["count"] == 2
