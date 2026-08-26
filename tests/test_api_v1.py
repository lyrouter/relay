"""API-1…API-6 · the public surface, driven as HTTP.

Every test here goes through the real ASGI app with a real token, because the
properties worth checking at this layer only exist end to end:

* the **tenant comes from the token** and cannot be steered by the request (§8.2);
* **authority is re-derived per request** — a demotion narrows a token that was
  minted earlier (API-1);
* **idempotency and ``external_ref``** each stop the duplicate the other cannot
  (§8.4), and ``If-Match`` makes a lost update impossible;
* the **error shape is problem+json everywhere**, including 401/403/404/409/422
  and the reserved namespaces' 501 (§8.7);
* **cross-tenant is 404**, never 403 — the remaining half of MT-6, which could not
  be written until tokens existed.

``base_url`` is https because the session cookie used to *create* tokens is
Secure; the API itself is header-authenticated and does not care.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.api.problems import CONTENT_TYPE, PROBLEM_BASE
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.domain.enums import Role, TicketStatus

from .conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
ADMIN = "admin@zerosone.test"

ALL_SCOPES = ["tickets:read", "tickets:write", "comments:write", "meta:read"]


# --------------------------------------------------------------- fixtures


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
def other_tenant():
    """A second tenant with its own Admin — MT-6's other side."""
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="平台组",
            tenant_slug="platform",
            admin_email="admin@platform.test",
            admin_password=PASSWORD,
        )
    )


@pytest.fixture
def client():
    with TestClient(create_app(), base_url="https://testserver") as test_client:
        yield test_client


def login(email: str = ADMIN, password: str = PASSWORD) -> TestClient:
    """A logged-in browser session, with its own cookie jar."""
    client = TestClient(create_app(), base_url="https://testserver")
    response = client.post("/web/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client


def mint(
    session: TestClient,
    *,
    name: str = "gateway webui",
    principal_type: str = "service",
    scopes: list[str] | None = None,
    lifetime_days: int | None = 365,
) -> str:
    response = session.post(
        "/web/tokens",
        json={
            "name": name,
            "principal_type": principal_type,
            "scopes": scopes if scopes is not None else ALL_SCOPES,
            "lifetime_days": lifetime_days,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["plaintext"]


@pytest.fixture
def admin(gateway):
    return login()


@pytest.fixture
def service_token(admin) -> str:
    return mint(admin)


@pytest.fixture
def api(client, service_token) -> TestClient:
    """An API client carrying a full-scope service token."""
    client.headers["Authorization"] = f"Bearer {service_token}"
    return client


def problem_of(response) -> dict:
    assert response.headers["content-type"].startswith(CONTENT_TYPE), response.headers
    body = response.json()
    assert body["type"].startswith(PROBLEM_BASE)
    assert body["status"] == response.status_code
    assert body["title"]
    return body


def a_ticket(api: TestClient, **overrides) -> dict:
    payload = {"type": "bug", "title": "provider 侧 429 突增"} | overrides
    response = api.post("/api/v1/tickets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------- API-1


def test_a_token_is_shown_once_and_stored_hashed(admin):
    """Hash-only storage: the list endpoint can never hand the plaintext back."""
    created = admin.post(
        "/web/tokens",
        json={"name": "alertmanager", "principal_type": "service", "scopes": ["tickets:write"]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    plaintext = body["plaintext"]
    assert plaintext.startswith("rly_s_")
    assert body["token"]["token_prefix"].startswith("rly_s_")
    # The fingerprint is a prefix of the token, not the token.
    assert plaintext.startswith(body["token"]["token_prefix"])
    assert len(body["token"]["token_prefix"]) < len(plaintext)

    listed = admin.get("/web/tokens").json()
    assert [one["name"] for one in listed] == ["alertmanager"]
    assert "plaintext" not in listed[0]
    assert plaintext not in admin.get("/web/tokens").text


def test_a_personal_token_carries_the_prefix_that_says_so(admin):
    body = admin.post(
        "/web/tokens",
        json={"name": "my laptop", "principal_type": "user", "scopes": ["tickets:read"]},
    ).json()
    assert body["plaintext"].startswith("rly_u_")


def test_a_request_with_no_token_is_refused_as_problem_json(client):
    response = client.get("/api/v1/tickets")
    assert response.status_code == 401
    assert problem_of(response)["type"].endswith("invalid_token")


def test_a_forged_token_is_refused(client):
    client.headers["Authorization"] = "Bearer rly_s_not-a-real-token"
    assert client.get("/api/v1/tickets").status_code == 401


def test_a_revoked_token_stops_working_immediately(admin, client):
    plaintext = mint(admin)
    client.headers["Authorization"] = f"Bearer {plaintext}"
    assert client.get("/api/v1/tickets").status_code == 200

    token_id = admin.get("/web/tokens").json()[0]["id"]
    assert admin.delete(f"/web/tokens/{token_id}").status_code == 204

    assert client.get("/api/v1/tickets").status_code == 401


def test_a_scope_the_token_lacks_is_refused_with_403(admin, client):
    """The capability table is coarse, so scopes are checked at the route too:
    ``meta:read`` alone must not read tickets."""
    client.headers["Authorization"] = f"Bearer {mint(admin, scopes=['meta:read'])}"
    assert client.get("/api/v1/meta/labels").status_code == 200

    refused = client.get("/api/v1/tickets")
    assert refused.status_code == 403
    assert problem_of(refused)["type"].endswith("permission_denied")


def test_a_write_scope_is_needed_to_write(admin, client):
    client.headers["Authorization"] = f"Bearer {mint(admin, scopes=['tickets:read'])}"
    refused = client.post("/api/v1/tickets", json={"type": "bug", "title": "x"})
    assert refused.status_code == 403


def test_last_used_at_is_recorded(admin, api):
    a_ticket(api)
    assert admin.get("/web/tokens").json()[0]["last_used_at"] is not None


def test_a_personal_token_is_narrowed_by_the_owners_role(
    gateway, admin, client, user_factory
):
    """API-1's central property: the role is read per request, never frozen into
    the credential. Demoting the owner narrows a token minted while they were a
    Member — otherwise R-2's account review would be checking the account while
    the credential kept working."""
    from relay.domain.enums import UserStatus

    member_id = user_factory(
        gateway.tenant_id, "mem@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    # The member sets their own password by accepting reality: bootstrap only made
    # the Admin, so mint the personal token as the member via the service layer.
    from relay.app.api_tokens import ApiTokenService
    from relay.context import tenant_scope
    from relay.domain.enums import PrincipalType, TokenScope

    from .conftest import context_for

    with tenant_scope(context_for(gateway.tenant_id, member_id)):
        issued = ApiTokenService().issue(
            "member laptop",
            PrincipalType.USER,
            frozenset({TokenScope.TICKETS_WRITE}),
        )

    client.headers["Authorization"] = f"Bearer {issued.plaintext}"
    created = client.post("/api/v1/tickets", json={"type": "task", "title": "会前准备"})
    assert created.status_code == 201, created.text

    demoted = admin.put(f"/web/admin/users/{member_id}/role", json={"role": "guest"})
    assert demoted.status_code == 204, demoted.text

    refused = client.post("/api/v1/tickets", json={"type": "task", "title": "第二张"})
    assert refused.status_code == 403, refused.text


def test_deactivating_the_owner_kills_their_token(gateway, admin, client, user_factory):
    """R-2's offboarding case. A token that outlived the account would make
    "deactivate on the last day" a comforting ritual rather than a control."""
    from relay.app.api_tokens import ApiTokenService
    from relay.context import tenant_scope
    from relay.domain.enums import PrincipalType, TokenScope, UserStatus

    from .conftest import context_for

    member_id = user_factory(
        gateway.tenant_id, "leaver@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    with tenant_scope(context_for(gateway.tenant_id, member_id)):
        issued = ApiTokenService().issue(
            "leaver laptop", PrincipalType.USER, frozenset({TokenScope.TICKETS_READ})
        )
    client.headers["Authorization"] = f"Bearer {issued.plaintext}"
    assert client.get("/api/v1/tickets").status_code == 200

    assert admin.post(f"/web/admin/users/{member_id}/deactivation").status_code == 200
    assert client.get("/api/v1/tickets").status_code == 401


def test_a_guest_cannot_create_a_token(gateway, user_factory):
    from relay.app.api_tokens import ApiTokenService
    from relay.app.errors import PermissionDenied
    from relay.context import tenant_scope
    from relay.domain.enums import PrincipalType, TokenScope, UserStatus

    from .conftest import context_for

    guest = user_factory(
        gateway.tenant_id, "guest@zerosone.test", role=Role.GUEST, status=UserStatus.ACTIVE
    )
    with tenant_scope(context_for(gateway.tenant_id, guest)):
        with pytest.raises(PermissionDenied):
            ApiTokenService().issue(
                "guest token", PrincipalType.USER, frozenset({TokenScope.TICKETS_READ})
            )


def test_an_expiring_token_is_listed_before_it_expires(admin):
    mint(admin, name="expires soon", lifetime_days=10)
    mint(admin, name="expires later", lifetime_days=200)
    names = [one["name"] for one in admin.get("/web/tokens/expiring").json()]
    assert names == ["expires soon"]


# --------------------------------------------------------- §8.2 · tenancy


def test_a_tenant_id_in_the_body_is_a_400(api, other_tenant):
    """§8.2 is explicit that this is 400 rather than 422: the shape is fine, and
    the request is one we refuse to serve."""
    response = api.post(
        "/api/v1/tickets",
        json={"type": "bug", "title": "x", "tenant_id": str(other_tenant.tenant_id)},
    )
    assert response.status_code == 400
    assert problem_of(response)["type"].endswith("tenant_in_request")


def test_a_tenant_id_in_the_query_is_a_400(api, other_tenant):
    response = api.get(f"/api/v1/tickets?tenant_id={other_tenant.tenant_id}")
    assert response.status_code == 400


def test_a_tenant_id_inside_ai_context_is_the_callers_data(api):
    """Top-level only. A field named ``tenant_id`` *inside* ``ai_context`` is data
    the tenant configured, not an attempt to route the request — refusing it would
    break a payload doing nothing wrong."""
    response = api.post(
        "/api/v1/tickets",
        json={"type": "bug", "title": "x", "ai_context": {"tenant_id": ["abc"]}},
    )
    # Refused by the ai_context field config (§7.3), not by the tenancy rule.
    assert response.status_code == 422
    assert problem_of(response)["type"].endswith("validation_failed")


def test_a_token_cannot_reach_another_tenants_ticket(api, other_tenant):
    """**MT-6's remaining half.** 404, never 403: a 403 would confirm that RL-1
    exists over there, which is the fact the boundary exists to hide."""
    from relay.app.tickets.service import NewTicket, TicketService
    from relay.context import tenant_scope
    from relay.domain.enums import TicketType

    from .conftest import context_for

    with tenant_scope(context_for(other_tenant.tenant_id, other_tenant.admin_user_id)):
        theirs = TicketService().create(NewTicket(type=TicketType.BUG, title="别人的单"))

    response = api.get(f"/api/v1/tickets/{theirs.number}")
    assert response.status_code == 404
    assert problem_of(response)["type"].endswith("not_found")

    patched = api.patch(
        f"/api/v1/tickets/{theirs.number}",
        json={"title": "改别人的"},
        headers={"If-Match": str(theirs.rev)},
    )
    assert patched.status_code == 404

    listed = api.get("/api/v1/tickets").json()
    assert listed["items"] == []


def test_the_permalink_carries_the_tenant_segment(api, gateway):
    """S-12. The first consumer stores this URL, so a missing segment would be a
    breaking change in somebody else's database."""
    ticket = a_ticket(api)
    assert ticket["url"].endswith(f"/gateway/t/{ticket['number']}")


# ------------------------------------------------------------------- API-2


def test_the_whole_flow_works_with_only_a_token(api):
    """§8.7: create → list → patch → transition → comment, credential only."""
    ticket = a_ticket(api)
    key = ticket["key"]

    assert any(one["key"] == key for one in api.get("/api/v1/tickets").json()["items"])

    patched = api.patch(
        f"/api/v1/tickets/{key}",
        json={"priority": "p1"},
        headers={"If-Match": str(ticket["rev"])},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["priority"] == "p1"
    assert patched.json()["rev"] == ticket["rev"] + 1

    moved = api.post(
        f"/api/v1/tickets/{key}/transitions",
        json={"to": "in_progress"},
        headers={"If-Match": str(patched.json()["rev"])},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "in_progress"

    commented = api.post(f"/api/v1/tickets/{key}/comments", json={"body": "已在处理"})
    assert commented.status_code == 201, commented.text
    listed = api.get(f"/api/v1/tickets/{key}/comments").json()
    assert [one["body"] for one in listed] == ["已在处理"]

    history = api.get(f"/api/v1/tickets/{key}/history").json()
    assert [one["to_status"] for one in history] == ["todo", "in_progress"]
    # §8.4: the column Phase 2's loop guard reads.
    assert {one["origin"] for one in history} == {"api"}
    assert {one["actor_type"] for one in history} == {"integration"}


def test_a_ticket_can_be_addressed_by_key_or_number(api):
    ticket = a_ticket(api)
    assert api.get(f"/api/v1/tickets/{ticket['number']}").json()["id"] == ticket["id"]
    assert api.get(f"/api/v1/tickets/{ticket['key']}").json()["id"] == ticket["id"]


def test_a_uuid_is_not_part_of_the_public_contract(api):
    """The web surface accepts ids because its own responses hand them out. The
    frozen surface stays narrower — everything it accepts, it accepts forever."""
    ticket = a_ticket(api)
    response = api.get(f"/api/v1/tickets/{ticket['id']}")
    assert response.status_code == 422
    assert problem_of(response)


def test_the_list_pages_with_an_opaque_cursor(api):
    for index in range(3):
        a_ticket(api, title=f"第 {index} 张")

    first = api.get("/api/v1/tickets?limit=2").json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]

    second = api.get(f"/api/v1/tickets?limit=2&cursor={first['next_cursor']}").json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    keys = {one["key"] for one in first["items"]} | {one["key"] for one in second["items"]}
    assert len(keys) == 3


def test_a_malformed_cursor_is_refused_rather_than_restarting(api):
    """Silently returning page one for a corrupt cursor is how a paging bug
    becomes an infinite loop over the same rows."""
    response = api.get("/api/v1/tickets?cursor=not-a-cursor")
    assert response.status_code == 422
    assert problem_of(response)


def test_updated_since_is_inclusive(api):
    """A poll must find the row whose timestamp equals the stored watermark."""
    ticket = a_ticket(api)
    found = api.get(
        "/api/v1/tickets", params={"updated_since": ticket["updated_at"]}
    ).json()["items"]
    assert [one["key"] for one in found] == [ticket["key"]]


def test_updated_since_survives_an_unencoded_offset(api):
    """The value a consumer has is our own ``updated_at`` — ``…+08:00``. Pasted
    into a query string unencoded, its ``+`` arrives as a space, and answering 422
    to somebody who copied our output is a bad first integration."""
    ticket = a_ticket(api)
    raw = ticket["updated_at"].replace("+", " ")
    found = api.get(f"/api/v1/tickets?updated_since={raw}").json()["items"]
    assert [one["key"] for one in found] == [ticket["key"]]


def test_a_nonsense_updated_since_is_refused(api):
    """Tolerant about the offset, strict about everything else: a misparsed
    watermark means a poll that misses changes or replays the whole board."""
    response = api.get("/api/v1/tickets?updated_since=yesterday")
    assert response.status_code == 422
    assert problem_of(response)


def test_filters_narrow_the_list(api):
    first = a_ticket(api, title="p1 的", priority="p1")
    a_ticket(api, title="p3 的", priority="p3")
    found = api.get("/api/v1/tickets?priority=p1").json()["items"]
    assert [one["key"] for one in found] == [first["key"]]


def test_meta_users_never_returns_an_email(api):
    """§8.3 states it as a rule. A directory of everyone's work address is the
    most reusable thing a service token could leak."""
    users = api.get("/api/v1/meta/users").json()
    assert users
    for one in users:
        assert set(one) == {"id", "display_name"}
    assert "zerosone.test" not in api.get("/api/v1/meta/users").text


def test_meta_exposes_the_ai_context_schema(api):
    fields = api.get("/api/v1/meta/ticket-fields").json()
    assert fields
    assert {"key", "label", "type", "domain_scope", "visible"} == set(fields[0])


def test_the_reserved_namespaces_answer_501(api):
    """Claimed, not built. 501 rather than 404, so an integrator asks when it
    ships instead of building a workaround."""
    for path in ("/api/v1/logs/anything", "/api/v1/search?q=x"):
        response = api.get(path)
        assert response.status_code == 501, path
        assert problem_of(response)["type"].endswith("not_implemented")


# ------------------------------------------------------------------- API-3


def test_the_same_idempotency_key_three_times_makes_one_ticket(api):
    """§8.7, verbatim. The replay returns the *first* response, not a new one."""
    key = str(uuid.uuid4())
    payload = {"type": "bug", "title": "重放三次"}
    first = api.post("/api/v1/tickets", json=payload, headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text

    for _ in range(2):
        again = api.post("/api/v1/tickets", json=payload, headers={"Idempotency-Key": key})
        assert again.status_code == 201
        assert again.json()["id"] == first.json()["id"]

    titles = [one["title"] for one in api.get("/api/v1/tickets").json()["items"]]
    assert titles.count("重放三次") == 1


def test_the_same_key_with_a_different_body_is_refused(api):
    """Returning the first response for a different request would be silent data
    loss: the caller would be told their create succeeded."""
    key = str(uuid.uuid4())
    api.post(
        "/api/v1/tickets",
        json={"type": "bug", "title": "第一个"},
        headers={"Idempotency-Key": key},
    )
    response = api.post(
        "/api/v1/tickets",
        json={"type": "bug", "title": "另一个"},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 422
    assert problem_of(response)


def test_a_refused_create_releases_its_key(api):
    """The caller did nothing wrong: they must be able to fix the body and retry
    with the same key."""
    key = str(uuid.uuid4())
    bad = api.post(
        "/api/v1/tickets",
        json={"type": "bug", "title": "坏标签", "label_ids": [str(uuid.uuid4())]},
        headers={"Idempotency-Key": key},
    )
    assert bad.status_code == 404, bad.text

    good = api.post(
        "/api/v1/tickets",
        json={"type": "bug", "title": "修好了"},
        headers={"Idempotency-Key": key},
    )
    assert good.status_code == 201, good.text


def test_a_repeated_external_ref_returns_the_existing_ticket(api):
    """The *other* defence (§8.4): this one survives a new Idempotency-Key, which
    is what "the user clicked submit three times" actually looks like."""
    ref = {"system": "gateway-webui", "id": "feedback-99871"}
    payload = {
        "type": "bug",
        "title": "网关反馈",
        "external_ref": {"system": ref["system"], "external_id": ref["id"]},
    }
    first = api.post(
        "/api/v1/tickets", json=payload, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert first.status_code == 201

    second = api.post(
        "/api/v1/tickets", json=payload, headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    # 200, not 201 and not an error: the caller needs to be told which ticket to
    # look at rather than given something to retry.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]


def test_a_patch_without_if_match_is_refused(api):
    ticket = a_ticket(api)
    response = api.patch(f"/api/v1/tickets/{ticket['key']}", json={"title": "改一下"})
    assert response.status_code == 422
    assert problem_of(response)


def test_a_stale_if_match_is_a_409_carrying_the_current_rev(api):
    """§8.7: concurrent PATCH, one wins, and the loser learns the current rev so
    it can re-read exactly once rather than poll."""
    ticket = a_ticket(api)
    stale = ticket["rev"]

    won = api.patch(
        f"/api/v1/tickets/{ticket['key']}",
        json={"title": "赢的那次"},
        headers={"If-Match": str(stale)},
    )
    assert won.status_code == 200

    lost = api.patch(
        f"/api/v1/tickets/{ticket['key']}",
        json={"title": "输的那次"},
        headers={"If-Match": str(stale)},
    )
    assert lost.status_code == 409
    body = problem_of(lost)
    assert body["rev"] == won.json()["rev"]

    assert api.get(f"/api/v1/tickets/{ticket['key']}").json()["title"] == "赢的那次"


def test_a_transition_also_requires_if_match(api):
    ticket = a_ticket(api)
    assert (
        api.post(f"/api/v1/tickets/{ticket['key']}/transitions", json={"to": "done"}).status_code
        == 422
    )


def test_a_reason_is_required_for_blocked(api):
    ticket = a_ticket(api)
    response = api.post(
        f"/api/v1/tickets/{ticket['key']}/transitions",
        json={"to": "blocked"},
        headers={"If-Match": str(ticket["rev"])},
    )
    assert response.status_code == 422
    assert problem_of(response)


def test_an_illegal_transition_is_refused(api):
    ticket = a_ticket(api)
    response = api.post(
        f"/api/v1/tickets/{ticket['key']}/transitions",
        json={"to": "in_review"},
        headers={"If-Match": str(ticket["rev"])},
    )
    assert response.status_code == 422


def test_a_done_ticket_can_be_reopened(api):
    """S-23: S1 has no terminal state, and the public API is where consumers find
    that out. ``rev`` keeps counting; the number does not change."""
    ticket = a_ticket(api)
    rev = ticket["rev"]
    for target in ("in_progress", "in_review", "done", "todo"):
        moved = api.post(
            f"/api/v1/tickets/{ticket['key']}/transitions",
            json={"to": target},
            headers={"If-Match": str(rev)},
        )
        assert moved.status_code == 200, (target, moved.text)
        rev = moved.json()["rev"]
    reopened = api.get(f"/api/v1/tickets/{ticket['key']}").json()
    assert reopened["status"] == TicketStatus.TODO
    assert reopened["number"] == ticket["number"]


# ------------------------------------------------------------------- API-6


def test_a_submitter_is_recorded_without_becoming_the_reporter(api):
    """§8.8's first rule. ``reporter`` stays the machine principal (S-10) because
    gateway users are not Relay accounts; ``submitter`` is display only."""
    ticket = a_ticket(
        api,
        title="登录之后一直转圈",
        submitter={"name": "王莉", "email": "li.wang@customer.example", "external_id": "u-7781"},
        source="gateway-webui",
        external_ref={"system": "gateway-webui", "external_id": "feedback-1"},
    )
    assert ticket["submitter"]["name"] == "王莉"
    assert ticket["source"] == "gateway-webui"
    # A service token has no user, so there is no person to mis-attribute this to.
    assert ticket["reporter_id"] is None

    fetched = api.get(f"/api/v1/tickets/{ticket['key']}").json()
    assert fetched["submitter"]["external_id"] == "u-7781"


def test_the_feedback_default_is_a_p2_bug(api):
    """F-6 ③: the submitter does not choose the priority; the assignee triages."""
    ticket = a_ticket(api, title="反馈", submitter={"name": "王莉"})
    assert (ticket["type"], ticket["priority"]) == ("bug", "p2")


def test_a_submitter_needs_a_name(api):
    response = api.post(
        "/api/v1/tickets",
        json={"type": "bug", "title": "x", "submitter": {"email": "a@b.com"}},
    )
    assert response.status_code == 422


def test_polling_a_ticket_gives_status_and_last_update(api):
    """F-6 ① — the consumer shows progress to the submitter from this one read."""
    ticket = a_ticket(api, submitter={"name": "王莉"})
    body = api.get(f"/api/v1/tickets/{ticket['key']}").json()
    assert body["status"] == "todo"
    assert body["updated_at"]


def test_a_support_ticket_keeps_its_category_and_markers(api):
    """S-26: the gateway is the true source; Relay stores the copy's markers."""
    ticket = a_ticket(
        api,
        title="账单对不上",
        category="billing",
        source="gateway-webui",
        labels=["from-gateway-webui"],
        submitter={"name": "王莉", "email": "li.wang@customer.example"},
        external_ref={
            "system": "gateway-webui",
            "external_id": "tkt_abc",
            "external_url": "https://gateway.internal/support/tkt_abc",
        },
    )
    assert ticket["category"] == "billing"
    assert ticket["source"] == "gateway-webui"
    assert "from-gateway-webui" in ticket["labels"]
    assert ticket["external_ref"]["external_id"] == "tkt_abc"

    listed = api.get("/api/v1/tickets", params={"category": "billing"}).json()["items"]
    assert [one["key"] for one in listed] == [ticket["key"]]
    assert api.get("/api/v1/tickets", params={"category": "presale"}).json()["items"] == []


def test_a_service_token_can_attach_a_file_to_a_ticket(api, monkeypatch, tmp_path):
    """S-26: screenshots sync through /api/v1. A service principal has no user
    row, so uploaded_by is null rather than a fabricated person (S-10)."""
    from relay.api.v1 import tickets as tickets_route
    from relay.infra.blob.filesystem import FilesystemBlobStore

    store = FilesystemBlobStore(root=str(tmp_path / "blobs"))
    monkeypatch.setattr(tickets_route, "blob_store", lambda: store)

    ticket = a_ticket(api)
    uploaded = api.post(
        f"/api/v1/tickets/{ticket['key']}/attachments",
        files={"file": ("screen.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["filename"] == "screen.png"
    assert body["uploaded_by"] is None
    assert body["scan_state"] == "skipped"

    listed = api.get(f"/api/v1/tickets/{ticket['key']}/attachments").json()
    assert [one["id"] for one in listed] == [body["id"]]

    link = api.get(
        f"/api/v1/tickets/{ticket['key']}/attachments/{body['id']}/link"
    ).json()["url"]
    assert link.startswith("/blobs/")


# ------------------------------------------------------------------- API-5


def test_every_response_reports_the_rate_limit(api):
    """S-14: instrument first, tighten after two weeks. A client can only slow
    itself down if it can see its budget."""
    response = api.get("/api/v1/tickets")
    assert response.headers["X-RateLimit-Limit"] == "600"
    assert int(response.headers["X-RateLimit-Remaining"]) < 600
    assert int(response.headers["X-RateLimit-Reset"]) <= 60


def test_writes_have_their_own_tighter_quota(api):
    a_ticket(api)
    response = api.post("/api/v1/tickets", json={"type": "task", "title": "第二张"})
    assert response.headers["X-RateLimit-Limit"] == "120"


def test_exceeding_the_quota_is_a_429_with_retry_after(admin, client, monkeypatch):
    from relay.app import api_rate_limit

    monkeypatch.setattr(api_rate_limit, "READ_PER_MINUTE", 2)
    client.headers["Authorization"] = f"Bearer {mint(admin, scopes=['tickets:read'])}"

    assert client.get("/api/v1/tickets").status_code == 200
    assert client.get("/api/v1/tickets").status_code == 200
    refused = client.get("/api/v1/tickets")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    body = problem_of(refused)
    assert body["limit"] == 2
    assert body["scope"] == "read"


def test_validation_failures_carry_field_errors(api):
    response = api.post("/api/v1/tickets", json={"type": "nonsense", "title": ""})
    assert response.status_code == 422
    body = problem_of(response)
    assert body["errors"]
    assert any("type" in one["field"] for one in body["errors"])


def test_an_unknown_path_under_the_api_is_still_problem_json(api):
    """Starlette's own 404 — the one response that leaks ``{"detail": ...}`` if
    the handler is registered on FastAPI's subclass instead."""
    response = api.get("/api/v1/nope")
    assert response.status_code == 404
    assert problem_of(response)


def test_one_request_costs_exactly_one_of_the_quota(api):
    """The dependency graph must not charge twice.

    ``scoped(...)`` wraps ``require_token``, and FastAPI caches a dependency per
    request — so a route with a scope annotation consumes one unit, not one per
    dependency in the chain. Asserted rather than assumed because the failure is
    quiet: a client's real budget would be a fraction of the documented one, and
    the only symptom is 429s that arrive "too early".
    """
    first = int(api.get("/api/v1/tickets").headers["X-RateLimit-Remaining"])
    second = int(api.get("/api/v1/tickets").headers["X-RateLimit-Remaining"])
    assert first - second == 1
