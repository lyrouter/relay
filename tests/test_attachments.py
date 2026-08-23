"""LOG-5 · attachments: the tenant-prefixed key, the permission check, the link.

The object store is the one thing RLS does not cover (S-11), so these are the
tests that stand in for a policy. Two of them are the point of the file: a key
always contains its tenant, and a link is only issued after the *log's* share
level says yes.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.errors import NotFound, PermissionDenied, ValidationFailed
from relay.app.logs.attachments import AttachmentService
from relay.app.logs.service import LogService
from relay.app.tickets.service import NewTicket, TicketService
from relay.context import tenant_scope
from relay.domain.enums import Role, ShareLevel, TicketType, UserStatus
from relay.infra.blob.filesystem import BlobTooLarge, FilesystemBlobStore, InvalidSignature
from relay.ports.blob import tenant_prefix

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"


@pytest.fixture
def store(tmp_path):
    return FilesystemBlobStore(root=str(tmp_path / "blobs"))


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


def as_user(gateway, user_id):
    return tenant_scope(context_for(gateway.tenant_id, user_id))


def png() -> io.BytesIO:
    return io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64)


# ------------------------------------------------------------------ the key


def test_the_key_contains_the_tenant(gateway, author, store):
    """S-11's only isolation mechanism for the object store: a key minted for one
    tenant cannot name an object in another."""
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        AttachmentService(store).upload("log", log.id, "screen.png", "image/png", png())
        attachments = AttachmentService(store).list_for("log", log.id)

    assert len(attachments) == 1
    from sqlalchemy import select

    from relay.infra.db.models import Attachment
    from relay.infra.db.session import tenant_session

    with tenant_session(context_for(gateway.tenant_id)) as session:
        key = session.scalars(select(Attachment.blob_key)).one()
    assert key.startswith(tenant_prefix(gateway.tenant_id))


def test_two_uploads_of_the_same_filename_do_not_collide(gateway, author, store):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        service = AttachmentService(store)
        one = service.upload("log", log.id, "screen.png", "image/png", png())
        two = service.upload("log", log.id, "screen.png", "image/png", png())
    assert one.id != two.id


def test_a_path_traversal_filename_cannot_escape_the_store(gateway, author, store):
    """The stored name is display metadata; the key's random segment is what
    makes it unique, so being aggressive here costs nothing."""
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        view = AttachmentService(store).upload(
            "log", log.id, "../../etc/passwd", "text/plain", io.BytesIO(b"x")
        )
    assert view.filename == "etcpasswd" or "/" not in view.filename
    assert ".." not in view.filename


# ------------------------------------------------------------------ limits


def test_an_unsupported_type_is_refused(gateway, author, store):
    """An allowlist, not a blocklist: a blocklist of dangerous types is a list
    somebody has to keep complete forever."""
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        with pytest.raises(ValidationFailed):
            AttachmentService(store).upload(
                "log", log.id, "run.sh", "application/x-sh", io.BytesIO(b"#!/bin/sh")
            )


def test_an_oversized_upload_is_refused_while_streaming(gateway, author, tmp_path):
    """The limit costs one chunk, not a whole file written and then rejected."""
    small = FilesystemBlobStore(root=str(tmp_path / "blobs"), max_bytes=32)
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        with pytest.raises(BlobTooLarge):
            AttachmentService(small).upload(
                "log", log.id, "big.bin", "application/zip", io.BytesIO(b"0" * 1024)
            )


def test_a_failed_upload_leaves_no_object_behind(gateway, author, tmp_path):
    """A half-written object would pass a size check on read and hand somebody a
    truncated file."""
    root = tmp_path / "blobs"
    small = FilesystemBlobStore(root=str(root), max_bytes=32)
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        with pytest.raises(BlobTooLarge):
            AttachmentService(small).upload(
                "log", log.id, "big.bin", "application/zip", io.BytesIO(b"0" * 1024)
            )
    assert not list(root.rglob("big.bin"))


def test_the_scan_hook_records_that_nothing_looked(gateway, author, store):
    """``skipped``, not ``clean``. Reporting clean would be a lie that survives
    into an incident review."""
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        view = AttachmentService(store).upload(
            "log", log.id, "screen.png", "image/png", png()
        )
    assert view.scan_state == "skipped"


# ------------------------------------------------------------- permissions


def test_only_the_author_can_attach_to_their_log(gateway, author, colleague, store):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容", share_level=ShareLevel.TENANT)
    with as_user(gateway, colleague):
        with pytest.raises(PermissionDenied):
            AttachmentService(store).upload(
                "log", log.id, "screen.png", "image/png", png()
            )


def test_a_link_is_refused_for_a_log_you_cannot_read(gateway, author, colleague, store):
    """The check comes **before** the signature. Relying on the signature alone
    would make the link itself the credential — the "URL is unguessable" model
    S-11 rejects."""
    with as_user(gateway, author):
        log = LogService().create("私密排查", "内容")
        attachment = AttachmentService(store).upload(
            "log", log.id, "screen.png", "image/png", png()
        )
    with as_user(gateway, colleague):
        with pytest.raises(NotFound):
            AttachmentService(store).link(attachment.id)


def test_a_link_is_issued_once_the_log_is_shared(gateway, author, colleague, store):
    with as_user(gateway, author):
        service = LogService()
        log = service.create("排查", "内容")
        attachment = AttachmentService(store).upload(
            "log", log.id, "screen.png", "image/png", png()
        )
        service.set_share_level(log.id, ShareLevel.TENANT)
    with as_user(gateway, colleague):
        assert AttachmentService(store).link(attachment.id).startswith("/blobs/")


def test_listing_attachments_follows_the_same_rule(gateway, author, colleague, store):
    with as_user(gateway, author):
        log = LogService().create("私密排查", "内容")
        AttachmentService(store).upload("log", log.id, "screen.png", "image/png", png())
    with as_user(gateway, colleague):
        with pytest.raises(NotFound):
            AttachmentService(store).list_for("log", log.id)


def test_a_ticket_attachment_needs_ticket_write(gateway, author, store, user_factory):
    guest = user_factory(
        gateway.tenant_id, "guest@zerosone.test", role=Role.GUEST, status=UserStatus.ACTIVE
    )
    with as_user(gateway, author):
        ticket = TicketService().create(NewTicket(type=TicketType.BUG, title="网关 502"))
    with as_user(gateway, guest):
        with pytest.raises(PermissionDenied):
            AttachmentService(store).upload(
                "ticket", ticket.id, "screen.png", "image/png", png()
            )


def test_another_tenants_owner_is_not_found(gateway, author, store):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        theirs = LogService().create("他们的", "内容")
    with as_user(gateway, author):
        with pytest.raises(NotFound):
            AttachmentService(store).upload(
                "log", theirs.id, "screen.png", "image/png", png()
            )


def test_an_unsupported_owner_type_is_refused(gateway, author, store):
    with as_user(gateway, author):
        with pytest.raises(ValidationFailed):
            AttachmentService(store).upload(
                "comment", gateway.tenant_id, "x.png", "image/png", png()
            )


# ------------------------------------------------------------ signed links


def test_a_signed_link_verifies(store):
    url = store.signed_url("t/x/abc/screen.png")
    key, _, query = url.removeprefix("/blobs/").partition("?")
    params = dict(pair.split("=") for pair in query.split("&"))
    store.verify(key, int(params["expires"]), params["sig"])


def test_a_tampered_signature_is_refused(store):
    url = store.signed_url("t/x/abc/screen.png")
    key, _, query = url.removeprefix("/blobs/").partition("?")
    params = dict(pair.split("=") for pair in query.split("&"))
    with pytest.raises(InvalidSignature):
        store.verify(key, int(params["expires"]), "0" * 64)


def test_a_link_for_a_different_key_is_refused(store):
    """The key is inside the signature, so a valid signature cannot be moved onto
    another object."""
    url = store.signed_url("t/x/abc/screen.png")
    _, _, query = url.removeprefix("/blobs/").partition("?")
    params = dict(pair.split("=") for pair in query.split("&"))
    with pytest.raises(InvalidSignature):
        store.verify("t/y/other/screen.png", int(params["expires"]), params["sig"])


def test_an_expired_link_stops_working(store):
    """Five minutes (S-11). What makes a leaked link stop being a capability."""
    url = store.signed_url("t/x/abc/screen.png", ttl=dt.timedelta(seconds=1))
    key, _, query = url.removeprefix("/blobs/").partition("?")
    params = dict(pair.split("=") for pair in query.split("&"))
    later = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)
    with pytest.raises(InvalidSignature):
        store.verify(key, int(params["expires"]), params["sig"], now=later)


def test_a_key_that_escapes_the_root_is_refused(store):
    """``blob_key`` round-trips through the database and a URL before it comes
    back here, which is exactly the shape of a path-traversal bug."""
    with pytest.raises(ValueError):
        store.open("../../../etc/passwd")


# ------------------------------------------------------------------ delete


def test_deleting_removes_the_row_and_the_object(gateway, author, store, tmp_path):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容")
        service = AttachmentService(store)
        view = service.upload("log", log.id, "screen.png", "image/png", png())
        service.delete(view.id)
        assert service.list_for("log", log.id) == []
    assert not list((tmp_path / "blobs").rglob("screen.png"))


def test_a_colleague_cannot_delete_your_attachment(gateway, author, colleague, store):
    with as_user(gateway, author):
        log = LogService().create("排查", "内容", share_level=ShareLevel.TENANT)
        view = AttachmentService(store).upload(
            "log", log.id, "screen.png", "image/png", png()
        )
    with as_user(gateway, colleague):
        with pytest.raises(PermissionDenied):
            AttachmentService(store).delete(view.id)
