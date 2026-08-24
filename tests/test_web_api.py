"""WEB-1…WEB-4 · the HTTP layer, driven as HTTP.

Every test here goes through the real ASGI application — ``TestClient``, real
cookies, real headers — because the things worth checking at this layer are the
ones that do not exist when a route function is called directly:

* the **tenant context** actually reaching the use case (the async-dependency /
  ``ContextVar`` mechanics in ``dependencies.py``: get that wrong and every
  request raises ``MissingTenantContext``);
* the **error shape**, on all four paths that produce one (§8.6);
* the **cookie** rules — CSRF, the MFA gate, and a session dying the moment an
  Admin deactivates the account behind it (R-2);
* ``If-Match`` and the cursor, which are conventions the frontend has to obey and
  the public API will inherit.

``base_url`` is **https** on purpose: the session cookie is ``Secure`` by default,
and over http httpx would silently refuse to send it back — which is exactly the
first hour a developer loses if they miss ``RELAY_SESSION_COOKIE_SECURE=false``.
Testing the secure configuration is also testing the production one.
"""

from __future__ import annotations

import io
import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.api.dependencies import SESSION_COOKIE
from relay.api.problems import CONTENT_TYPE, PROBLEM_BASE
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.domain.enums import Role, UserStatus
from relay.infra.blob.filesystem import FilesystemBlobStore

from .conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
ADMIN = "admin@zerosone.test"


@pytest.fixture
def gateway():
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email=ADMIN,
            admin_password=PASSWORD,
        )
    )


@pytest.fixture
def client():
    """An anonymous client. Distinct from ``admin`` on purpose: a test that needs
    two identities needs two cookie jars, and sharing one produced a failure that
    read as a permission bug ("the lock holder cannot heartbeat") when it was
    really the second login overwriting the first."""
    with TestClient(create_app(), base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def admin(gateway):
    """A logged-in Admin, with its own cookie jar."""
    client = TestClient(create_app(), base_url="https://testserver")
    response = client.post(
        "/web/auth/login", json={"email": ADMIN, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "mfa_required": False,
        "password_reminder": False,
        "unfamiliar_network": False,
    }
    return client


def a_ticket(client, **overrides) -> dict:
    payload = {"type": "bug", "title": "网关 502"} | overrides
    response = client.post("/web/tickets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def a_log(client, **overrides) -> dict:
    payload = {"title": "排查记录", "body": "第一版"} | overrides
    response = client.post("/web/logs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def problem_of(response) -> dict:
    """Assert the response *is* a problem document, and hand it back."""
    assert response.headers["content-type"].startswith(CONTENT_TYPE), response.headers
    body = response.json()
    assert body["type"].startswith(PROBLEM_BASE)
    assert body["status"] == response.status_code
    assert body["title"]
    return body


# --------------------------------------------------- the context gets through


def test_a_session_reaches_the_use_case_with_the_right_tenant(admin, gateway):
    """The single most load-bearing test in this file.

    ``require_session`` must be an **async** dependency: a sync one runs in a
    worker thread whose context is a copy, so the ``TenantContext`` it set would
    be invisible to the endpoint and every request would fail. If this passes,
    that wiring is right.
    """
    response = admin.get("/web/session")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == ADMIN
    assert body["role"] == Role.ADMIN.value
    assert body["tenant"]["slug"] == "gateway"
    assert body["tenant"]["id"] == str(gateway.tenant_id)
    # S-12: the slug ships from day one because every permalink carries it.
    assert body["unread_notifications"] == 0
    # The UI hides what the service would refuse rather than re-deriving §5.4.
    assert "user_manage" in body["capabilities"]


def test_no_cookie_is_a_401_problem(client, gateway):
    response = client.get("/web/session")
    assert response.status_code == 401
    assert problem_of(response)["type"] == f"{PROBLEM_BASE}session_expired"


def test_a_stale_cookie_is_the_same_401(client, gateway):
    """One answer for missing, revoked, idled out and aged out: whether a token
    was ever real is not something the response should teach."""
    client.cookies.set(SESSION_COOKIE, "not-a-real-token", domain="testserver")
    response = client.get("/web/session")
    assert response.status_code == 401
    assert problem_of(response)["type"] == f"{PROBLEM_BASE}session_expired"


def test_logout_clears_the_cookie_and_the_session(admin):
    assert admin.post("/web/auth/logout").status_code == 204
    assert admin.get("/web/session").status_code == 401


def test_logout_works_without_a_usable_session(client, gateway):
    """No session dependency on logout: the only way out of a stuck state must
    not be clearing cookies by hand."""
    assert client.post("/web/auth/logout").status_code == 204


# ------------------------------------------------------------------- CSRF


def test_a_cross_site_post_is_refused(admin):
    response = admin.post(
        "/web/logs",
        json={"title": "从别的站点发出的"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert problem_of(response)["type"] == f"{PROBLEM_BASE}permission_denied"


def test_the_configured_origin_is_accepted(admin):
    response = admin.post(
        "/web/logs",
        json={"title": "从自己的站点发出的"},
        headers={"Origin": "https://relay.internal"},
    )
    assert response.status_code == 201


def test_a_cross_site_get_is_not_refused(admin):
    """A read cannot be forged into an action, and refusing it would break
    embedding without protecting anything."""
    response = admin.get("/web/session", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


# ------------------------------------------------------------- the MFA gate


def test_a_half_open_session_cannot_do_anything_else(client, gateway):
    """AC-3 opens the session before the code is verified. Every route except
    the TOTP one must refuse it — and with ``mfa_required``, which is a different
    instruction from "log in again"."""
    assert client.post(
        "/web/auth/login", json={"email": ADMIN, "password": PASSWORD}
    ).status_code == 200

    # Enroll through the real routes: the secret is handed out, proved, and only
    # then stored (AC-3).
    enrollment = client.post("/web/auth/totp/enrollment").json()
    secret = enrollment["secret"]
    assert secret in enrollment["provisioning_uri"]
    assert client.post(
        "/web/auth/totp/enrollment/confirm",
        json={"secret": secret, "code": pyotp.TOTP(secret).now()},
    ).status_code == 204
    client.post("/web/auth/logout")

    # From here the password alone opens a session that can do exactly one thing.
    response = client.post("/web/auth/login", json={"email": ADMIN, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["mfa_required"] is True

    refused = client.get("/web/session")
    assert refused.status_code == 401
    assert problem_of(refused)["type"] == f"{PROBLEM_BASE}mfa_required"

    accepted = client.post("/web/auth/totp", json={"code": pyotp.TOTP(secret).now()})
    assert accepted.status_code == 200
    assert client.get("/web/session").status_code == 200


# --------------------------------------------------------------- error shape


def test_a_missing_log_is_a_404_problem(admin):
    response = admin.get(f"/web/logs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert problem_of(response)["type"] == f"{PROBLEM_BASE}not_found"


def test_a_schema_failure_is_422_with_field_errors(admin):
    """§8.6: keep FastAPI's status, normalise the body. The fields land in
    ``errors[]`` rather than in a shape of their own."""
    response = admin.post("/web/tickets", json={"type": "not-a-type", "title": ""})
    assert response.status_code == 422
    body = problem_of(response)
    assert body["type"] == f"{PROBLEM_BASE}validation_failed"
    assert any("type" in error["field"] for error in body["errors"])


def test_a_use_case_refusal_is_also_422(admin):
    """An empty title is refused by the service, not by the schema — and it comes
    back in the same shape, which is the whole point of the handler."""
    response = admin.post("/web/logs", json={"title": "   "})
    assert response.status_code == 422
    assert problem_of(response)["type"] == f"{PROBLEM_BASE}validation_failed"


def test_a_permission_refusal_is_403_with_a_next_step(client, gateway, user_factory):
    member = user_factory(
        gateway.tenant_id, "dev@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    _login_as(client, gateway, member)
    response = client.post(
        "/web/admin/invitations", json={"email": "new@zerosone.test", "role": "member"}
    )
    assert response.status_code == 403
    body = problem_of(response)
    assert body["type"] == f"{PROBLEM_BASE}permission_denied"
    # Design §2: a user-facing failure names the next step.
    assert "管理员" in body["title"]
    assert body["capability"] == "user_manage"


def test_rate_limiting_carries_retry_after(admin):
    """VERIFICATION_RESEND is three per fifteen minutes; the fourth is a 429 with
    ``Retry-After``, which §8.6 requires and a client cannot guess."""
    for _ in range(3):
        assert admin.post(
            "/web/auth/verification/resend", json={"email": ADMIN}
        ).status_code == 200
    response = admin.post("/web/auth/verification/resend", json={"email": ADMIN})
    assert response.status_code == 429
    assert problem_of(response)["type"] == f"{PROBLEM_BASE}rate_limited"
    assert int(response.headers["retry-after"]) > 0


def test_an_unknown_route_is_a_problem_document_too(admin):
    """FastAPI's own 404 goes through the handler as well, or the API would have
    two error formats — the exact thing §8.6 is about."""
    response = admin.get("/web/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(CONTENT_TYPE)


# ------------------------------------------------------------------- tickets


def test_the_ticket_round_trip(admin):
    created = a_ticket(admin, description="上游 429 突增")
    assert created["key"].startswith("RL-")
    assert created["rev"] == 1

    # By id, by number, and by key — one lookup path for the frontend.
    for key in (created["id"], str(created["number"]), created["key"]):
        assert admin.get(f"/web/tickets/{key}").json()["id"] == created["id"]

    patched = admin.patch(
        f"/web/tickets/{created['key']}",
        json={"title": "上游 429 突增（已定位）"},
        headers={"If-Match": str(created["rev"])},
    )
    assert patched.status_code == 200
    assert patched.json()["rev"] == 2

    moved = admin.post(
        f"/web/tickets/{created['key']}/transitions",
        json={"to": "in_progress"},
        headers={"If-Match": "2"},
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "in_progress"

    comment = admin.post(
        f"/web/tickets/{created['key']}/comments", json={"body": "已复现"}
    )
    assert comment.status_code == 201

    history = admin.get(f"/web/tickets/{created['key']}/history").json()
    assert [row["to_status"] for row in history] == ["todo", "in_progress"]
    # §8.4: the column Phase 2's loop guard reads. A web change is a web change.
    assert history[-1]["origin"] == "web"
    assert history[-1]["actor_type"] == "user"


def test_a_patch_without_if_match_is_refused(admin):
    created = a_ticket(admin)
    response = admin.patch(f"/web/tickets/{created['key']}", json={"title": "改了"})
    assert response.status_code == 422
    assert "If-Match" in problem_of(response)["title"]


def test_a_stale_if_match_is_409_carrying_the_current_rev(admin):
    """API-3's contract, and the reason ``rev`` exists: the loser of a race gets
    an error naming the version it needs, so it can re-read exactly once."""
    created = a_ticket(admin)
    admin.patch(
        f"/web/tickets/{created['key']}",
        json={"title": "第一次"},
        headers={"If-Match": "1"},
    )
    response = admin.patch(
        f"/web/tickets/{created['key']}",
        json={"title": "第二次"},
        headers={"If-Match": "1"},
    )
    assert response.status_code == 409
    body = problem_of(response)
    assert body["type"] == f"{PROBLEM_BASE}conflict"
    assert body["rev"] == 2


def test_a_transition_that_needs_a_reason_says_so(admin):
    created = a_ticket(admin)
    response = admin.post(
        f"/web/tickets/{created['key']}/transitions",
        json={"to": "blocked"},
        headers={"If-Match": "1"},
    )
    assert response.status_code == 422
    assert "原因" in problem_of(response)["title"]


def test_the_cursor_pages_without_repeating(admin):
    for i in range(5):
        a_ticket(admin, title=f"第 {i}")

    first = admin.get("/web/tickets", params={"limit": 2}).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]

    second = admin.get(
        "/web/tickets", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    seen = {one["id"] for one in first["items"]} | {one["id"] for one in second["items"]}
    assert len(seen) == 4

    last = admin.get("/web/tickets", params={"limit": 50}).json()
    assert len(last["items"]) == 5
    # A short page is the last page: handing out a cursor would cost the client
    # one empty request.
    assert last["next_cursor"] is None


def test_a_broken_cursor_is_refused_rather_than_restarting(admin):
    """Silently returning page one for a corrupt cursor is how a paging bug
    becomes an infinite loop over the same rows."""
    response = admin.get("/web/tickets", params={"cursor": "!!!not-base64!!!"})
    assert response.status_code == 422


def test_a_guest_does_not_see_the_board_over_http(admin, client, gateway, user_factory):
    """S-21, end to end — the filter is in SQL, so no route can forget it."""
    contractor = user_factory(
        gateway.tenant_id, "vendor@zerosone.test", role=Role.GUEST, status=UserStatus.ACTIVE
    )
    theirs = a_ticket(admin, title="内部的活")
    mine = a_ticket(admin, title="外部的活", assignee_id=str(contractor))

    _login_as(client, gateway, contractor)
    listed = client.get("/web/tickets").json()["items"]
    assert [one["id"] for one in listed] == [mine["id"]]
    assert client.get(f"/web/tickets/{theirs['key']}").status_code == 404


# ---------------------------------------------------------------------- logs


def test_autosave_sends_only_the_body_and_keeps_the_title(admin):
    """The absent-vs-null distinction, which is what ``UNSET`` exists for. Get
    this wrong and every autosave blanks the title."""
    created = a_log(admin)
    saved = admin.patch(f"/web/logs/{created['id']}", json={"body": "第二版"})
    assert saved.status_code == 200
    assert saved.json() == created | {
        "body": "第二版",
        "current_version": 2,
        "updated_at": saved.json()["updated_at"],
    }


def test_versions_diff_and_rollback(admin):
    created = a_log(admin)
    admin.patch(f"/web/logs/{created['id']}", json={"body": "第二版"})

    versions = admin.get(f"/web/logs/{created['id']}/versions").json()
    assert [one["version_no"] for one in versions] == [2, 1]

    diff = admin.get(
        f"/web/logs/{created['id']}/diff", params={"from_version": 1, "to_version": 2}
    ).json()
    assert {line["op"] for line in diff} >= {"add", "remove"}

    rolled = admin.post(f"/web/logs/{created['id']}/rollback", json={"to_version": 1})
    assert rolled.status_code == 200
    # §6.2: rollback appends. History is never rewritten.
    assert rolled.json()["body"] == "第一版"
    assert rolled.json()["current_version"] == 3


def test_sharing_and_grants(admin, gateway, user_factory):
    colleague = user_factory(
        gateway.tenant_id, "bob@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    created = a_log(admin)
    shared = admin.put(
        f"/web/logs/{created['id']}/share", json={"share_level": "named"}
    )
    assert shared.status_code == 200
    assert shared.json()["share_level"] == "named"

    assert admin.post(
        f"/web/logs/{created['id']}/grants", json={"user_id": str(colleague)}
    ).status_code == 204
    assert admin.get(f"/web/logs/{created['id']}/grants").json() == [str(colleague)]
    assert admin.delete(
        f"/web/logs/{created['id']}/grants/{colleague}"
    ).status_code == 204
    assert admin.get(f"/web/logs/{created['id']}/grants").json() == []


def test_the_edit_lock_reports_who_holds_it(admin, client, gateway, user_factory):
    """S-7: a refusal has to name the holder and the countdown, or the user has
    nothing to do with it."""
    created = a_log(admin)
    assert admin.post(f"/web/logs/{created['id']}/lock").status_code == 200

    other = user_factory(
        gateway.tenant_id, "eve@zerosone.test", role=Role.ADMIN, status=UserStatus.ACTIVE
    )
    _login_as(client, gateway, other)
    refused = client.post(f"/web/logs/{created['id']}/lock")
    assert refused.status_code == 409
    assert problem_of(refused)["type"] == f"{PROBLEM_BASE}conflict"

    assert admin.post(f"/web/logs/{created['id']}/lock/heartbeat").status_code == 200
    assert admin.delete(f"/web/logs/{created['id']}/lock").status_code == 204
    assert admin.get(f"/web/logs/{created['id']}/lock").json() is None


def test_the_knowledge_marker_and_its_count(admin):
    """LOG-9 / S-16: checked **and** body ≥ 300 characters. One constant, so
    INT-8's dashboard and the checkbox cannot disagree."""
    short = a_log(admin, title="短笔记", body="太短")
    long = a_log(admin, title="长记录", body="内容" * 200)
    assert admin.put(f"/web/logs/{short['id']}/knowledge", json={"marked": True}).status_code == 200
    assert admin.put(f"/web/logs/{long['id']}/knowledge", json={"marked": True}).status_code == 200
    assert admin.get("/web/logs/knowledge-count").json() == {"count": 1}


# --------------------------------------------------------------- attachments


def test_upload_then_fetch_through_a_signed_link(admin, monkeypatch, tmp_path):
    from relay.api.web import attachments as attachments_route

    store = FilesystemBlobStore(root=str(tmp_path))
    monkeypatch.setattr(attachments_route, "blob_store", lambda: store)

    created = a_log(admin)
    uploaded = admin.post(
        "/web/attachments",
        data={"owner_type": "log", "owner_id": created["id"]},
        files={"file": ("trace.txt", io.BytesIO(b"stack trace"), "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["size"] == len(b"stack trace")
    # The scan hook says what it did. Never "clean" when nothing scanned.
    assert attachment["scan_state"] == "skipped"

    listed = admin.get(
        "/web/attachments", params={"owner_type": "log", "owner_id": created["id"]}
    ).json()
    assert [one["id"] for one in listed] == [attachment["id"]]

    link = admin.get(f"/web/attachments/{attachment['id']}/link").json()["url"]
    fetched = admin.get(link)
    assert fetched.status_code == 200
    assert fetched.content == b"stack trace"
    assert "inline" in fetched.headers["content-disposition"]

    # S-11: the signature is what stops a handed-out link becoming a permanent
    # capability. One answer for forged, expired and missing.
    tampered = link.replace("sig=", "sig=0")
    assert admin.get(tampered).status_code == 404


# ------------------------------------------------------- notifications, search


def test_an_assignment_shows_up_in_the_assignees_inbox(admin, client, gateway, user_factory):
    member = user_factory(
        gateway.tenant_id, "dev@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    a_ticket(admin, assignee_id=str(member))

    _login_as(client, gateway, member)
    assert client.get("/web/notifications/unread-count").json() == {"unread": 1}
    items = client.get("/web/notifications").json()
    assert items[0]["type"] == "assignment"
    assert items[0]["folded_count"] == 1

    assert client.post(f"/web/notifications/{items[0]['notification_id']}/read").status_code == 204
    assert client.get("/web/notifications/unread-count").json() == {"unread": 0}
    # And the boot payload agrees with the endpoint.
    assert client.get("/web/session").json()["unread_notifications"] == 0


def test_search_finds_a_log_and_a_ticket(admin):
    a_log(admin, title="网关超时排查", body="upstream 超时")
    a_ticket(admin, title="网关超时")
    hits = admin.get("/web/search", params={"q": "超时"}).json()
    assert {one["kind"] for one in hits["hits"]} == {"log", "ticket"}


def test_an_empty_search_returns_nothing_not_everything(admin):
    a_log(admin)
    assert admin.get("/web/search", params={"q": "   "}).json() == {"hits": [], "total": 0}


# ------------------------------------------------------------ meta and admin


def test_labels_and_iterations(admin):
    label = admin.post("/web/meta/labels", json={"name": "网关", "color": "#ff0000"})
    assert label.status_code == 201
    assert admin.get("/web/meta/labels").json()[0]["name"] == "网关"

    renamed = admin.patch(
        f"/web/meta/labels/{label.json()['id']}", json={"name": "网关团队"}
    )
    assert renamed.json()["name"] == "网关团队"

    iteration = admin.post("/web/meta/iterations", json={"name": "S1"})
    assert iteration.status_code == 201
    closed = admin.put(
        f"/web/meta/iterations/{iteration.json()['id']}/closed", json={"closed": True}
    )
    assert closed.json()["closed"] is True
    assert admin.get("/web/meta/iterations", params={"include_closed": False}).json() == []


def test_a_bad_colour_is_refused(admin):
    """It is rendered into a style attribute, so an unvalidated value is CSS
    injection rather than a cosmetic problem."""
    response = admin.post("/web/meta/labels", json={"name": "坏色", "color": "red; }"})
    assert response.status_code == 422


def test_the_ticket_field_config_is_readable(admin):
    fields = admin.get("/web/meta/ticket-fields").json()
    keys = {one["key"] for one in fields}
    assert "trace_id" in keys
    # §7.3: the gated fields are off unless the tenant was granted the scope,
    # and this tenant was bootstrapped without it.
    assert "gateway_version" not in keys
    assert all(one["visible"] for one in fields)


def test_deactivating_a_user_kills_their_session_now(admin, client, gateway, user_factory):
    """R-2, end to end. Without SSO this is the *only* thing that removes access,
    so "at their next login" would not be good enough."""
    member = user_factory(
        gateway.tenant_id, "leaver@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    _login_as(client, gateway, member)
    assert client.get("/web/session").status_code == 200

    response = admin.post(f"/web/admin/users/{member}/deactivation")
    assert response.status_code == 200
    assert response.json() == {"sessions_ended": 1}

    assert client.get("/web/session").status_code == 401


def test_the_directory_offers_handles_but_not_addresses(admin, gateway, user_factory):
    user_factory(
        gateway.tenant_id, "lisa@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    people = admin.get("/web/users").json()
    handles = {one["handle"] for one in people}
    assert "lisa" in handles
    assert not any("@" in one["handle"] for one in people)
    assert all("email" not in one for one in people)


def test_the_startup_check_names_the_deployment_mistakes(monkeypatch):
    """Owner actions O-1, O-2 and O-5, as a check that can fail.

    Every setting here leaves the application *working*: mail silently unsent,
    attachment links signed with a key published in this repository, webhook
    deliveries signed with another one, a session cookie without ``Secure``, and
    attachments on a container's local disk. Nothing can be raised without making
    the development default unusable, so a log line is the whole mechanism — and a
    startup warning nobody has seen fire is the same as no check.

    The count is asserted, not just the contents: a warning that stops firing
    because somebody reordered the function is the failure this catches.
    """
    from relay.api import wiring
    from relay.config import settings

    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "blob_signing_key", wiring.DEV_SIGNING_KEY)
    monkeypatch.setattr(settings, "webhook_signing_key", wiring.DEV_WEBHOOK_KEY)
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    monkeypatch.setattr(settings, "blob_carrier", "filesystem")
    warnings = wiring.check_configuration()
    assert len(warnings) == 5
    assert any("O-2" in one for one in warnings)
    assert any("O-1" in one for one in warnings)
    assert any("RELAY_WEBHOOK_SIGNING_KEY" in one for one in warnings)
    assert any("RELAY_BLOB_CARRIER" in one for one in warnings)

    # Silence is only available to a *fully* configured deployment — which
    # includes the deployed carrier. A development run is expected to be noisy;
    # what must never happen is a production run that is quiet while wrong.
    monkeypatch.setattr(settings, "smtp_host", "smtp.internal")
    monkeypatch.setattr(settings, "blob_signing_key", "a-real-key")
    monkeypatch.setattr(settings, "webhook_signing_key", "another-real-key")
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    monkeypatch.setattr(settings, "blob_carrier", "minio")
    monkeypatch.setattr(settings, "minio_endpoint", "http://minio.internal:9000")
    monkeypatch.setattr(settings, "minio_public_endpoint", "https://files.relay.example")
    monkeypatch.setattr(settings, "minio_access_key", "key")
    monkeypatch.setattr(settings, "minio_secret_key", "secret")
    assert wiring.check_configuration() == []


def test_a_minio_deployment_is_warned_about_its_signing_endpoint(monkeypatch):
    """S-25's blind spot #1, as a startup warning.

    A MinIO carrier signing links against its *internal* endpoint produces broken
    images with **nothing in the application log** — the browser fetches the object
    directly and never reaches us. This warning is the only place that becomes
    visible before a user finds it.
    """
    from relay.api import wiring
    from relay.config import settings

    monkeypatch.setattr(settings, "smtp_host", "smtp.internal")
    monkeypatch.setattr(settings, "blob_signing_key", "a-real-key")
    monkeypatch.setattr(settings, "webhook_signing_key", "another-real-key")
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    monkeypatch.setattr(settings, "blob_carrier", "minio")
    monkeypatch.setattr(settings, "minio_endpoint", "http://minio.internal:9000")
    monkeypatch.setattr(settings, "minio_public_endpoint", "")
    monkeypatch.setattr(settings, "minio_access_key", "key")
    monkeypatch.setattr(settings, "minio_secret_key", "secret")

    warnings = wiring.check_configuration()
    assert len(warnings) == 1
    assert "RELAY_MINIO_PUBLIC_ENDPOINT" in warnings[0]

    monkeypatch.setattr(settings, "minio_public_endpoint", "https://files.relay.example")
    assert wiring.check_configuration() == []


def test_healthz_needs_no_session(client):
    """Liveness, deliberately not a database check: a probe that queries
    PostgreSQL turns a slow database into a rolling restart."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --------------------------------------------------------------------- helper


def _login_as(client: TestClient, gateway, user_id) -> None:
    """Log a fixture-created user in by giving them a password first.

    ``user_factory`` writes rows directly (it predates the HTTP layer), so there
    is no password to log in with. Setting one through the real login path keeps
    the test honest about cookies and the MFA gate, which is the part being
    tested — as opposed to forging a session row.
    """
    from sqlalchemy import select

    from relay.infra.db.models import User
    from relay.infra.db.session import tenant_session
    from relay.infra.security.passwords import hash_password

    from .conftest import context_for

    with tenant_session(context_for(gateway.tenant_id)) as session:
        user = session.scalars(select(User).where(User.id == user_id)).one()
        user.password_hash = hash_password(PASSWORD)
        user.email_verified_at = user.created_at
        email = user.email
        session.commit()

    response = client.post("/web/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
