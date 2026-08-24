"""API-4 · the queue, the signature, the retries, and the SSRF rule (§8.5, S-13).

The transport is faked here on purpose: what is worth testing is the *ladder* —
which attempt is scheduled when, when a delivery gives up into the dead letter,
and that a replay puts it back. A retry schedule verified only against a real
endpoint is a retry schedule nobody verifies.

What is **not** faked is the destination rule. S-13 is a security decision, so the
tests drive the real predicate over the addresses that matter, including the
IPv4-mapped and metadata forms somebody will eventually try.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from relay.app import webhooks
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.errors import PermissionDenied, ValidationFailed
from relay.app.tickets.comments import CommentService
from relay.app.tickets.service import NewTicket, TicketService
from relay.app.webhooks import WebhookDispatcher, WebhookService
from relay.context import ActorType, Origin, TenantContext, tenant_scope
from relay.domain.destinations import address_is_forbidden, literal_refusal, resolved_refusal
from relay.domain.enums import (
    PrincipalType,
    Role,
    TicketStatus,
    TicketType,
    TokenScope,
    UserStatus,
    WebhookDeliveryState,
    WebhookState,
)

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
NOW = dt.datetime(2026, 8, 24, 10, 0, tzinfo=dt.UTC)

#: A destination the address rule accepts. Nothing listens there — the transport
#: is faked — so what matters is only that the host passes S-13.
GOOD_URL = "https://example.com/relay-hook"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Resolve ``example.com`` to a public address **without asking DNS**.

    The destination rule is exercised directly, over addresses, in the S-13
    section below; the registration and delivery tests only need a host that
    passes it. Patching the resolver here keeps them from failing on a CI box
    with no outbound DNS — a test that goes red because a resolver is missing
    teaches the team to ignore it.

    Tests that care about resolution patch over this again, and win, because
    ``monkeypatch`` unwinds in reverse.
    """
    monkeypatch.setattr(webhooks, "_resolve", lambda host: ["93.184.216.34"])


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
def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


class FakeTransport:
    """Records what was sent and answers with a scripted status."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.statuses = list(statuses or [200])
        self.calls: list[dict] = []

    def post(self, url, body, headers, *, timeout):
        self.calls.append(
            {"url": url, "body": body, "headers": headers, "timeout": timeout}
        )
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


class ExplodingTransport:
    """A transport failure — connection refused, DNS, TLS. Not an HTTP status."""

    def __init__(self) -> None:
        self.calls = 0

    def post(self, url, body, headers, *, timeout):
        self.calls += 1
        raise OSError("connection refused")


# ------------------------------------------------------------ S-13 · the rule


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata: the most valuable SSRF target
        "100.100.100.200",  # Alibaba Cloud metadata
        "::1",
        "fc00::1",
        "fe80::1",
        "0.0.0.0",
        "::ffff:10.0.0.1",  # IPv4-mapped: reads as global until it is unwrapped
    ],
)
def test_private_and_metadata_addresses_are_refused(address):
    import ipaddress

    assert address_is_forbidden(ipaddress.ip_address(address)), address


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1::1"])
def test_public_addresses_are_allowed(address):
    import ipaddress

    assert not address_is_forbidden(ipaddress.ip_address(address))


def test_a_literal_private_url_is_refused_without_dns():
    assert literal_refusal("http://169.254.169.254/latest/meta-data/") is not None
    assert literal_refusal("http://10.0.0.5:8080/hook") is not None
    assert literal_refusal("https://example.com/hook") is None


def test_a_non_http_scheme_is_refused():
    """``file://`` and ``gopher://`` are the classic SSRF escalations."""
    assert literal_refusal("file:///etc/passwd") is not None
    assert literal_refusal("gopher://internal/x") is not None


def test_every_resolved_address_is_checked_not_just_the_first():
    """A hostname with one public and one private record would otherwise pass
    here and connect to whichever the socket layer picked."""
    assert resolved_refusal(["93.184.216.34"]) is None
    assert resolved_refusal(["93.184.216.34", "10.0.0.1"]) is not None
    assert resolved_refusal([]) is not None


# ---------------------------------------------------- registration and access


def test_registering_returns_the_secret_once(as_admin):
    with as_admin:
        registered = WebhookService().register(GOOD_URL, ("ticket.created",))
        assert registered.secret
        assert registered.endpoint.state is WebhookState.ACTIVE

        listed = WebhookService().list()
        assert [one.url for one in listed] == [GOOD_URL]
        # There is no "show it again": the database holds a fingerprint, and the
        # secret is derived from a master key in the environment.
        assert not hasattr(listed[0], "secret")


def test_the_secret_is_reproducible_and_not_stored(as_admin):
    with as_admin:
        registered = WebhookService().register(GOOD_URL, ("ticket.created",))
        assert webhooks.secret_for(registered.endpoint.id, 1) == registered.secret


def test_rotating_changes_the_secret(as_admin):
    with as_admin:
        first = WebhookService().register(GOOD_URL, ("ticket.created",))
        second = WebhookService().rotate_secret(first.endpoint.id)
        assert second.secret != first.secret
        assert webhooks.secret_for(first.endpoint.id, 2) == second.secret


def test_a_private_destination_is_refused_at_registration(as_admin):
    with as_admin:
        with pytest.raises(ValidationFailed):
            WebhookService().register("http://127.0.0.1:9000/hook", ("ticket.created",))


def test_an_unknown_event_type_is_refused(as_admin):
    with as_admin:
        with pytest.raises(ValidationFailed):
            WebhookService().register(GOOD_URL, ("ticket.exploded",))


def test_no_events_is_refused(as_admin):
    """An endpoint subscribed to nothing is worse than an error: it looks
    configured and delivers silence."""
    with as_admin:
        with pytest.raises(ValidationFailed):
            WebhookService().register(GOOD_URL, ())


def test_a_member_cannot_register_a_webhook(gateway, user_factory):
    member = user_factory(
        gateway.tenant_id, "mem@zerosone.test", role=Role.MEMBER, status=UserStatus.ACTIVE
    )
    with tenant_scope(context_for(gateway.tenant_id, member)):
        with pytest.raises(PermissionDenied):
            WebhookService().register(GOOD_URL, ("ticket.created",))


def test_a_service_token_cannot_register_a_webhook(gateway):
    """No scope maps to ``WEBHOOK_MANAGE``, and this is why that matters: a
    machine principal that could add its own destination could copy every ticket
    out of the tenant with nobody approving anything."""
    ctx = TenantContext(
        tenant_id=gateway.tenant_id,
        actor_id=None,
        actor_type=ActorType.INTEGRATION,
        origin=Origin.API,
        scopes=frozenset(TokenScope),
    )
    with tenant_scope(ctx):
        with pytest.raises(PermissionDenied):
            WebhookService().register(GOOD_URL, ("ticket.created",))


# ------------------------------------------------------------- the outbox


def test_creating_a_ticket_queues_one_delivery_per_subscriber(as_admin):
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        WebhookService().register("https://example.com/second", ("ticket.created",))
        WebhookService().register("https://example.com/third", ("ticket.comment_created",))

        TicketService().create(NewTicket(type=TicketType.BUG, title="网关 502"), now=NOW)

        queued = WebhookService().deliveries()
        assert len(queued) == 2
        assert {one.event_type for one in queued} == {"ticket.created"}
        assert {one.state for one in queued} == {WebhookDeliveryState.PENDING}


def test_a_paused_endpoint_receives_nothing_new(as_admin):
    with as_admin:
        registered = WebhookService().register(GOOD_URL, ("ticket.created",))
        WebhookService().set_state(registered.endpoint.id, WebhookState.PAUSED)
        TicketService().create(NewTicket(type=TicketType.BUG, title="不发"), now=NOW)
        assert WebhookService().deliveries() == []


def test_each_lifecycle_event_is_queued(as_admin):
    with as_admin:
        WebhookService().register(GOOD_URL, webhooks.EVENT_TYPES)
        ticket = TicketService().create(
            NewTicket(type=TicketType.BUG, title="全链路"), now=NOW
        )
        updated = TicketService().update(
            ticket.id, expected_rev=ticket.rev, priority=None or ticket.priority, now=NOW
        )
        TicketService().transition(
            ticket.id, TicketStatus.IN_PROGRESS, expected_rev=updated.rev, now=NOW
        )
        CommentService().add(ticket.id, "在看了", now=NOW)

        types = [one.event_type for one in WebhookService().deliveries()]
        assert set(types) == set(webhooks.EVENT_TYPES)


def test_the_payload_carries_rev_and_the_actor(as_admin):
    """§8.5. ``rev`` is how an unordered consumer drops a stale event;
    ``actor_type`` is where Phase 2's loop guard reads from."""
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        ticket = TicketService().create(
            NewTicket(type=TicketType.BUG, title="带 rev"), now=NOW
        )

        transport = FakeTransport([200])
        WebhookDispatcher(transport).dispatch_batch(now=NOW)

    body = json.loads(transport.calls[0]["body"])
    assert body["ticket"]["rev"] == ticket.rev
    assert body["ticket"]["key"] == ticket.key
    assert body["actor"]["actor_type"] == "user"
    assert body["actor"]["origin"] == "web"
    assert body["event_type"] == "ticket.created"
    # The consumer's dedupe key, and it must be in the body rather than only in a
    # header — a consumer storing bodies has to be able to dedupe from one.
    assert uuid.UUID(body["event_id"])


def test_an_update_event_says_what_changed(as_admin):
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.updated",))
        ticket = TicketService().create(NewTicket(type=TicketType.BUG, title="旧标题"), now=NOW)
        TicketService().update(ticket.id, expected_rev=ticket.rev, title="新标题", now=NOW)

        transport = FakeTransport([200])
        WebhookDispatcher(transport).dispatch_batch(now=NOW)

    body = json.loads(transport.calls[0]["body"])
    assert body["changes"]["before"]["title"] == "旧标题"
    assert body["changes"]["after"]["title"] == "新标题"


# --------------------------------------------------------------- delivery


def test_a_delivered_event_is_signed_over_timestamp_and_body(as_admin):
    with as_admin:
        registered = WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="签名"), now=NOW)

        transport = FakeTransport([200])
        outcome = WebhookDispatcher(transport).dispatch_batch(now=NOW)
        assert outcome["delivered"] == 1

        call = transport.calls[0]
        timestamp = call["headers"]["X-Relay-Timestamp"]
        expected = webhooks.signature_of(registered.secret, timestamp, call["body"])
        assert call["headers"]["X-Relay-Signature"] == expected
        assert call["headers"]["X-Relay-Signature"].startswith("sha256=")
        assert call["headers"]["X-Relay-Event"] == "ticket.created"

        assert [one.state for one in WebhookService().deliveries()] == [
            WebhookDeliveryState.DELIVERED
        ]


def test_the_signature_does_not_verify_against_the_wrong_secret(as_admin):
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="错密钥"), now=NOW)
        transport = FakeTransport([200])
        WebhookDispatcher(transport).dispatch_batch(now=NOW)

    call = transport.calls[0]
    wrong = webhooks.signature_of(
        "not-the-secret", call["headers"]["X-Relay-Timestamp"], call["body"]
    )
    assert call["headers"]["X-Relay-Signature"] != wrong


def test_a_500_from_the_consumer_is_retried_on_the_ladder(as_admin):
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="重试"), now=NOW)

        transport = FakeTransport([500])
        outcome = WebhookDispatcher(transport).dispatch_batch(now=NOW)
        assert outcome == {"delivered": 0, "retrying": 1, "dead_letter": 0}

        queued = WebhookService().deliveries()[0]
        assert queued.state is WebhookDeliveryState.PENDING
        assert queued.attempt == 1
        # The second rung of 1m/5m/30m/2h/6h.
        assert queued.next_retry_at == NOW + webhooks.BACKOFF[1]
        assert "500" in queued.last_error


def test_a_transport_failure_is_also_a_retry(as_admin):
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="连不上"), now=NOW)

        transport = ExplodingTransport()
        assert WebhookDispatcher(transport).dispatch_batch(now=NOW)["retrying"] == 1
        assert "connection refused" in WebhookService().deliveries()[0].last_error


def test_a_delivery_not_yet_due_is_left_alone(as_admin):
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="等一分钟"), now=NOW)

        WebhookDispatcher(FakeTransport([500])).dispatch_batch(now=NOW)
        # One second later the retry is not due yet: nothing is claimed.
        transport = FakeTransport([200])
        outcome = WebhookDispatcher(transport).dispatch_batch(
            now=NOW + dt.timedelta(seconds=1)
        )
        assert outcome == {"delivered": 0, "retrying": 0, "dead_letter": 0}
        assert transport.calls == []


def test_five_failures_land_in_the_dead_letter_and_replay_brings_it_back(as_admin):
    """§8.7's last webhook clause. The dead letter is *kept*: a consumer that was
    down for a day needs its events back."""
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="死信"), now=NOW)

        transport = FakeTransport([500])
        at = NOW
        outcomes = []
        for _ in range(webhooks.MAX_ATTEMPTS):
            outcomes.append(WebhookDispatcher(transport).dispatch_batch(now=at))
            at += dt.timedelta(hours=7)  # past any rung of the ladder
        assert outcomes[-1]["dead_letter"] == 1
        assert len(transport.calls) == webhooks.MAX_ATTEMPTS

        dead = WebhookService().deliveries(state=WebhookDeliveryState.DEAD_LETTER)
        assert len(dead) == 1

        WebhookService().replay(dead[0].id)
        revived = WebhookService().deliveries()[0]
        assert revived.state is WebhookDeliveryState.PENDING
        assert revived.attempt == 0

        healthy = FakeTransport([200])
        assert WebhookDispatcher(healthy).dispatch_batch(now=at)["delivered"] == 1


def test_a_destination_that_turned_private_is_dead_lettered_without_retries(as_admin, monkeypatch):
    """DNS rebinding, checked again at send time (S-13).

    Fatal rather than retried: a refused destination will be refused identically
    in six hours, and five attempts at it is five chances for the attack rather
    than one.
    """
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="被重绑定"), now=NOW)

        monkeypatch.setattr(webhooks, "_resolve", lambda host: ["169.254.169.254"])
        transport = FakeTransport([200])
        outcome = WebhookDispatcher(transport).dispatch_batch(now=NOW)

        assert outcome["dead_letter"] == 1
        # Nothing was sent: the check is *before* the call.
        assert transport.calls == []
        dead = WebhookService().deliveries()[0]
        assert dead.attempt == 1
        assert "destination refused" in dead.last_error


def test_deleting_an_endpoint_takes_its_queue_with_it(as_admin):
    with as_admin:
        registered = WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="删端点"), now=NOW)
        assert WebhookService().deliveries()

        WebhookService().delete(registered.endpoint.id)
        assert WebhookService().list() == []
        assert WebhookService().deliveries() == []


def test_a_webhook_never_leaves_the_tenant_that_queued_it(gateway, as_admin):
    """The endpoint table is one of §4.3's two places the boundary reaches past
    RLS — so the queue it feeds has to stay inside it."""
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="平台组",
            tenant_slug="platform",
            admin_email="admin@platform.test",
            admin_password=PASSWORD,
        )
    )
    with as_admin:
        WebhookService().register(GOOD_URL, ("ticket.created",))
        TicketService().create(NewTicket(type=TicketType.BUG, title="我们的"), now=NOW)

    with tenant_scope(context_for(other.tenant_id, other.admin_user_id)):
        assert WebhookService().list() == []
        assert WebhookService().deliveries() == []
        # A dispatcher running for the other tenant must find nothing of ours.
        transport = FakeTransport([200])
        assert WebhookDispatcher(transport).dispatch_batch(now=NOW) == {
            "delivered": 0,
            "retrying": 0,
            "dead_letter": 0,
        }
        assert transport.calls == []


def test_a_service_principal_shows_as_the_machine_in_the_payload(gateway):
    """S-10: a ticket filed by a service token reports the machine principal, and
    the event says so — which is why INT-8 excludes these from people-metrics."""
    with tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id)):
        WebhookService().register(GOOD_URL, ("ticket.created",))

    ctx = TenantContext(
        tenant_id=gateway.tenant_id,
        actor_id=None,
        actor_type=ActorType.INTEGRATION,
        origin=Origin.API,
        scopes=frozenset({TokenScope.TICKETS_WRITE}),
    )
    with tenant_scope(ctx):
        TicketService().create(
            NewTicket(type=TicketType.BUG, title="告警建的单", source="alertmanager"), now=NOW
        )
        transport = FakeTransport([200])
        WebhookDispatcher(transport).dispatch_batch(now=NOW)

    body = json.loads(transport.calls[0]["body"])
    assert body["actor"]["actor_type"] == "integration"
    assert body["actor"]["id"] is None
    assert body["ticket"]["reporter_id"] is None
    assert body["ticket"]["source"] == "alertmanager"


def test_the_principal_type_enum_is_used_for_service_tokens():
    """Guards the value the payload and INT-8 both key on."""
    assert str(PrincipalType.SERVICE) == "service"
