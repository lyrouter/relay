"""INT-5 · the S1 critical flow, once, all the way through.

    signup → email verification → login → log → ticket → API write → notification

Every other test file checks one layer honestly. This one exists because the
**seams** between them are where S1 actually fails, and none of the other files
can see a seam: the verification token has to survive from a recorded mail into a
URL, the session cookie has to carry a tenant into a use case, a machine principal
has to file a ticket that a person then sees in their inbox.

It is deliberately one long test rather than several small ones. A flow assembled
from independent tests with shared fixtures is not the flow — it is the fixtures.
When this one fails, the assertion that failed names the step, which is the thing
a release conversation needs.

Two further scenarios follow it, both from §11.2's exit list rather than from a
layer: the gateway feedback round trip end to end (§8.8, the API's own exit
criterion), and the acceptance dashboard reporting on real activity (INT-8).
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.ports.mail import NullMailPort

from .conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
NEWCOMER_PASSWORD = "An0ther-Good-Passphrase"
ADMIN = "admin@zerosone.test"
NEWCOMER = "xiaoyu@zerosone.test"


@pytest.fixture
def gateway():
    """The tenant, bootstrapped the way O-4 does it on the real deployment."""
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email=ADMIN,
            admin_password=PASSWORD,
        )
    )


@pytest.fixture
def mail() -> NullMailPort:
    """Capture outbound mail, the way an unconfigured deployment does (O-2).

    Not a stub of our own: with ``RELAY_SMTP_HOST`` unset the composition root
    hands every route a ``NullMailPort``, which records instead of sending. So
    this fixture reaches for *that* adapter — the same one production gets when
    somebody forgets O-2 — and reads what it recorded. The verification link
    under test is therefore the one a real mailbox would have received.
    """
    from relay.api import wiring

    wiring.reset()
    port = wiring.mail_port()
    assert isinstance(port, NullMailPort), (
        "RELAY_SMTP_HOST is set in this environment, so mail is really being sent "
        "and this test cannot read it."
    )
    yield port
    wiring.reset()


@pytest.fixture
def client():
    with TestClient(create_app(), base_url="https://testserver") as test_client:
        yield test_client


def token_from(mail: NullMailPort, address: str) -> str:
    """Pull the verification token out of the recorded message.

    Parsed out of the body rather than read from the database, because "the link
    in the mail works" is the property under test — a token fetched from a table
    would pass even if the mail template dropped it.
    """
    messages = [one for one in mail.sent if address in one.to]
    assert messages, f"no mail was sent to {address}"
    body = messages[-1].text_body
    marker = "token="
    assert marker in body, body
    return body.split(marker, 1)[1].split()[0].strip().rstrip(".,)>\"'")


def test_the_s1_critical_flow(gateway, mail, client):
    """One person joins, writes, files, and is told about it. INT-5."""

    # 1 · Self-service signup (AC-1). The domain is allowlisted by bootstrap, so
    #     this lands in the right tenant without anybody choosing one.
    signup = client.post(
        "/web/auth/signup",
        json={
            "email": NEWCOMER,
            "password": NEWCOMER_PASSWORD,
            "display_name": "小雨",
        },
    )
    assert signup.status_code == 202, signup.text

    # 2 · The account cannot be used yet. This is the step O-2 gates: with no
    #     SMTP host the mail is recorded and nobody can get past here.
    too_early = client.post(
        "/web/auth/login", json={"email": NEWCOMER, "password": NEWCOMER_PASSWORD}
    )
    assert too_early.status_code in (401, 403), too_early.text

    # 3 · Email verification, using the token from the message itself.
    verified = client.post("/web/auth/verify", json={"token": token_from(mail, NEWCOMER)})
    assert verified.status_code == 200, verified.text

    # 4 · Login. The cookie is Secure, which is why base_url is https.
    login = client.post(
        "/web/auth/login", json={"email": NEWCOMER, "password": NEWCOMER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["mfa_required"] is False

    session = client.get("/web/session")
    assert session.status_code == 200, session.text
    me = session.json()
    assert me["tenant"]["slug"] == "gateway"
    assert me["display_name"] == "小雨"

    # 5 · Write a log, with an attachment and a version (LOG-4 / LOG-5).
    log = client.post("/web/logs", json={"title": "429 排查记录", "body": "第一版"})
    assert log.status_code == 201, log.text
    log_id = log.json()["id"]

    edited = client.patch(
        f"/web/logs/{log_id}",
        json={"body": "第一版\n加了一段结论：provider 侧限流。"},
    )
    assert edited.status_code == 200, edited.text
    assert len(client.get(f"/web/logs/{log_id}/versions").json()) >= 2

    upload = client.post(
        "/web/attachments",
        data={"owner_type": "log", "owner_id": log_id},
        files={"file": ("dashboard.png", io.BytesIO(b"\x89PNG grafana"), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    link = client.get(f"/web/attachments/{upload.json()['id']}/link")
    assert link.status_code == 200
    # Permission-checked, then signed (S-11) — never "the URL is unguessable".
    assert "sig=" in link.json()["url"]

    # 6 · File a ticket and assign it to the Admin, so a notification has a
    #     recipient who is not the actor (emit() drops self-notifications).
    ticket = client.post(
        "/web/tickets",
        json={
            "type": "bug",
            "title": "provider 侧 429 突增",
            "description": f"排查记录见日志 {log_id}",
            "assignee_id": str(gateway.admin_user_id),
        },
    )
    assert ticket.status_code == 201, ticket.text
    key = ticket.json()["key"]
    assert key.startswith("RL-")

    # 7 · The assignee is told (NT-1). In-app only in S1 (F-1), which is exactly
    #     why P-3 asks for "did anyone see it?" to be a trial observation.
    admin = TestClient(create_app(), base_url="https://testserver")
    assert admin.post(
        "/web/auth/login", json={"email": ADMIN, "password": PASSWORD}
    ).status_code == 200
    inbox = admin.get("/web/notifications").json()
    assert [one["type"] for one in inbox] == ["assignment"]
    assert admin.get("/web/notifications/unread-count").json()["unread"] == 1

    # 8 · An external system writes over the API (API-1/2/3) — the seam this file
    #     exists for: a token, a machine principal, and the same use case.
    minted = admin.post(
        "/web/tokens",
        json={
            "name": "gateway webui",
            "principal_type": "service",
            "scopes": ["tickets:read", "tickets:write", "comments:write"],
        },
    )
    assert minted.status_code == 201, minted.text
    api = TestClient(create_app(), base_url="https://testserver")
    api.headers["Authorization"] = f"Bearer {minted.json()['plaintext']}"

    commented = api.post(f"/api/v1/tickets/{key}/comments", json={"body": "网关侧已限流"})
    assert commented.status_code == 201, commented.text

    current = api.get(f"/api/v1/tickets/{key}").json()
    moved = api.post(
        f"/api/v1/tickets/{key}/transitions",
        json={"to": "in_progress"},
        headers={"If-Match": str(current["rev"])},
    )
    assert moved.status_code == 200, moved.text

    # 9 · The status change comes back to the people who care, and the history
    #     records that it was an integration rather than a person (§8.4).
    statuses = [one["type"] for one in admin.get("/web/notifications").json()]
    assert "status_change" in statuses
    history = api.get(f"/api/v1/tickets/{key}/history").json()
    assert history[-1]["actor_type"] == "integration"
    assert history[-1]["origin"] == "api"

    # 10 · And the person who reported it sees the move in their own inbox too.
    reporter_inbox = client.get("/web/notifications").json()
    assert any(one["type"] == "status_change" for one in reporter_inbox)


def test_the_gateway_feedback_round_trip(gateway, client):
    """§8.8 end to end — the API's own exit criterion (§11.2).

    A real submission lands as a ticket with ``submitter`` and a source label, and
    **a repeated submission does not create a second one**. Both halves matter:
    the first is what makes the feedback attributable, the second is what stops a
    user's triple-click from becoming three tickets.
    """
    admin = TestClient(create_app(), base_url="https://testserver")
    assert admin.post(
        "/web/auth/login", json={"email": ADMIN, "password": PASSWORD}
    ).status_code == 200
    minted = admin.post(
        "/web/tokens",
        json={
            "name": "gateway webui",
            "principal_type": "service",
            "scopes": ["tickets:read", "tickets:write"],
        },
    ).json()["plaintext"]

    api = TestClient(create_app(), base_url="https://testserver")
    api.headers["Authorization"] = f"Bearer {minted}"

    feedback_id = "feedback-99871"
    submission = {
        "type": "bug",
        "title": "切换模型之后一直转圈",
        "description": "截图见 https://gateway.internal/f/99871/shot.png",
        "external_ref": {
            "system": "gateway-webui",
            "external_id": feedback_id,
            "external_url": f"https://gateway.internal/f/{feedback_id}",
        },
        "submitter": {"name": "王莉", "email": "li.wang@zerosone.com"},
        "source": "gateway-webui",
    }

    first = api.post(
        "/api/v1/tickets", json=submission, headers={"Idempotency-Key": feedback_id}
    )
    assert first.status_code == 201, first.text
    filed = first.json()
    # S-10: the reporter is the machine, the submitter is the human. Putting the
    # human in ``reporter`` would require them to have a Relay account, and
    # gateway users do not and should not.
    assert filed["reporter_id"] is None
    assert filed["submitter"]["name"] == "王莉"
    assert filed["source"] == "gateway-webui"
    # S-12: the consumer stores this URL against its feedback record.
    assert filed["url"].endswith(f"/gateway/t/{filed['number']}")

    # The user clicks submit twice more; the WebUI's compensating job re-runs with
    # a fresh key. Neither creates a second ticket — that is why §8.4 wants both
    # ``Idempotency-Key`` (the network) and ``external_ref`` (the upstream).
    again = api.post(
        "/api/v1/tickets", json=submission, headers={"Idempotency-Key": feedback_id}
    )
    assert again.status_code == 201
    assert again.json()["id"] == filed["id"]

    compensating = api.post(
        "/api/v1/tickets", json=submission, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert compensating.status_code == 200, compensating.text
    assert compensating.json()["id"] == filed["id"]

    assert len(api.get("/api/v1/tickets").json()["items"]) == 1

    # F-6 ① · the consumer polls this one read to show the submitter progress.
    # Status and last-updated only; the WebUI is trusted not to show internal
    # comments, which is a constraint on it rather than on the API.
    polled = api.get(f"/api/v1/tickets/{filed['key']}").json()
    assert polled["status"] == "todo"
    assert polled["updated_at"]


def test_the_acceptance_dashboard_reports_real_activity(gateway, client):
    """INT-8, over the flow above rather than over fixtures.

    The assertion that matters is the denominator: a service-token ticket must not
    move the creator count, because one alerting script would otherwise look like
    the team's most productive member.
    """
    admin = TestClient(create_app(), base_url="https://testserver")
    assert admin.post(
        "/web/auth/login", json={"email": ADMIN, "password": PASSWORD}
    ).status_code == 200

    empty = admin.get("/web/admin/dashboard").json()
    assert empty["creators"]["activated_accounts"] == 1
    assert empty["creators"]["active_creators"] == 0
    assert empty["creators"]["share"] == 0.0

    assert admin.post("/web/logs", json={"title": "周报", "body": "x" * 400}).status_code == 201
    assert admin.post("/web/tickets", json={"type": "task", "title": "准备验收"}).status_code == 201

    after = admin.get("/web/admin/dashboard").json()
    assert after["creators"]["active_creators"] == 1
    assert after["creators"]["share"] == 1.0
    assert after["logs_this_week"] == 1
    assert after["tickets_this_week"] == 1
    assert after["tickets_by_status"]["todo"] == 1

    minted = admin.post(
        "/web/tokens",
        json={"name": "alertmanager", "principal_type": "service", "scopes": ["tickets:write"]},
    ).json()["plaintext"]
    api = TestClient(create_app(), base_url="https://testserver")
    api.headers["Authorization"] = f"Bearer {minted}"
    filed = api.post("/api/v1/tickets", json={"type": "bug", "title": "自动建的单"})
    assert filed.status_code == 201, filed.text

    machine = admin.get("/web/admin/dashboard").json()
    assert machine["tickets_this_week"] == 2
    # The point: the machine's ticket counted as work, not as a person.
    assert machine["creators"]["active_creators"] == 1


def test_the_knowledge_count_uses_the_products_own_rule(gateway, client):
    """S-16 / LOG-9: checked **and** ≥ 300 characters. One constant, two readers
    (the dashboard and ``/web/logs/knowledge-count``), so they cannot disagree."""
    admin = TestClient(create_app(), base_url="https://testserver")
    assert admin.post(
        "/web/auth/login", json={"email": ADMIN, "password": PASSWORD}
    ).status_code == 200

    short = admin.post("/web/logs", json={"title": "太短", "body": "结论：限流"}).json()
    long = admin.post("/web/logs", json={"title": "够长", "body": "x" * 400}).json()
    for one in (short, long):
        assert (
            admin.put(f"/web/logs/{one['id']}/knowledge", json={"marked": True}).status_code
            == 200
        )

    assert admin.get("/web/logs/knowledge-count").json()["count"] == 1
    assert admin.get("/web/admin/dashboard").json()["knowledge_candidates"] == 1
