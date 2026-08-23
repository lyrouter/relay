"""TKT-2 · the AI-context field registry, its gate, and write validation.

The gate is the interesting half. §7.3's warning is that the first team also
*builds* the gateway, so their requests all look generic — the discriminating
question is "could a team with no gateway of its own fill this in?". These tests
pin the answer for the two fields where it is no, and pin that the gate is data
(a tenant's own config rows) rather than a code path.
"""

from __future__ import annotations

import pytest

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.tickets.ai_context import (
    configured_fields,
    seed_field_config,
    validate_write,
)
from relay.domain.ai_context import (
    GATEWAY_FIELDS,
    GATEWAY_SCOPE,
    GENERIC_FIELDS,
    AiContextField,
    InvalidAiContext,
    validate,
)
from relay.domain.enums import AiContextFieldType
from relay.infra.db.session import tenant_session

from .conftest import context_for, requires_db

PASSWORD = "Corr3ct-Horse-Battery"


# ----------------------------------------------------------- the registry


def test_the_generic_set_is_exactly_what_the_design_reserves():
    """§7.3 row 1, copied by hand. A field appearing here without appearing in
    the design doc is the drift this test exists to catch."""
    assert {field.key for field in GENERIC_FIELDS} == {
        "trace_id",
        "provider",
        "model",
        "prompt_version",
        "deployment",
        "error_class",
        "eval_run",
        "token_cost",
        "blast_radius",
        "tenant",
    }


def test_only_the_two_gateway_fields_are_gated():
    """§7.3 row 2. Both fail the "no gateway of your own" test: a team without a
    gateway has no gateway version and no routing policy to name."""
    assert {field.key for field in GATEWAY_FIELDS} == {"gateway_version", "routing_policy"}
    assert all(field.domain_scope == GATEWAY_SCOPE for field in GATEWAY_FIELDS)


def test_no_generic_field_carries_a_scope():
    assert all(field.domain_scope is None for field in GENERIC_FIELDS)


# ------------------------------------------------------------- validation


def _fields(*keys):
    by_key = {field.key: field for field in GENERIC_FIELDS + GATEWAY_FIELDS}
    return tuple(by_key[key] for key in keys)


def test_a_declared_field_of_the_right_type_passes():
    result = validate(
        {"trace_id": ["abc", "def"], "token_cost": 1.25},
        _fields("trace_id", "token_cost"),
    )
    assert result == {"trace_id": ["abc", "def"], "token_cost": 1.25}


def test_an_undeclared_key_is_refused_rather_than_carried():
    """The load-bearing rule. Without ``extra="forbid"``, ``ai_context`` becomes
    the arbitrary JSON column §7.3 refuses — and the migration this task exists
    to avoid becomes necessary anyway, with the values already in production
    under keys nobody declared."""
    with pytest.raises(InvalidAiContext) as refused:
        validate({"whatever": "1"}, _fields("trace_id"))
    assert "whatever" in str(refused.value)


def test_the_refusal_lists_what_is_available():
    with pytest.raises(InvalidAiContext) as refused:
        validate({"routing_policy": "canary"}, _fields("trace_id", "model"))
    assert "trace_id" in str(refused.value)


def test_a_wrong_type_is_refused():
    with pytest.raises(InvalidAiContext):
        validate({"trace_id": "not-a-list"}, _fields("trace_id"))


def test_unset_keys_are_dropped_rather_than_stored_as_null():
    """Ten nulls per ticket is the same absence stored ten times, and it makes
    "which fields does this tenant actually use?" unanswerable from the data."""
    assert validate({"model": ["gpt"]}, _fields("model", "deployment")) == {"model": ["gpt"]}


def test_an_empty_write_is_empty():
    assert validate({}, GENERIC_FIELDS) == {}


def test_a_boolean_field_would_validate_as_one():
    """No boolean field is reserved today; the type mapping still has to work,
    or the first one added fails in a way that looks like a data problem."""
    flag = AiContextField("flagged", "标记", AiContextFieldType.BOOLEAN)
    assert validate({"flagged": True}, (flag,)) == {"flagged": True}


# -------------------------------------------------------- seeding and the gate


@requires_db
@pytest.mark.db
def test_a_plain_tenant_gets_the_generic_set_only():
    tenant = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_session(context_for(tenant.tenant_id)) as session:
        keys = {field.key for field in configured_fields(session)}
    assert keys == {field.key for field in GENERIC_FIELDS}
    assert "routing_policy" not in keys


@requires_db
@pytest.mark.db
def test_the_gateway_tenant_gets_the_gated_fields_too():
    tenant = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=PASSWORD,
            domain_scopes=(GATEWAY_SCOPE,),
        )
    )
    with tenant_session(context_for(tenant.tenant_id)) as session:
        keys = {field.key for field in configured_fields(session)}
    assert "routing_policy" in keys and "gateway_version" in keys


@requires_db
@pytest.mark.db
def test_a_tenant_without_the_scope_cannot_write_a_gated_field():
    """The gate is data, not a branch. There is no code path to forget: the
    validator is built from this tenant's own rows, so a field it has no row for
    does not exist as far as writing is concerned."""
    tenant = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_session(context_for(tenant.tenant_id)) as session:
        with pytest.raises(InvalidAiContext):
            validate_write(session, {"routing_policy": "canary"})
        # ...while a generic field is fine in the same tenant.
        assert validate_write(session, {"error_class": "timeout"}) == {"error_class": "timeout"}


@requires_db
@pytest.mark.db
def test_seeding_is_idempotent_and_additive():
    """A re-run adds nothing; granting a scope later adds only the new rows
    rather than resetting the visibility somebody has been tuning."""
    tenant = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_session(context_for(tenant.tenant_id)) as session:
        assert seed_field_config(session, tenant.tenant_id) == 0
        added = seed_field_config(
            session, tenant.tenant_id, domain_scopes=(GATEWAY_SCOPE,)
        )
        assert added == len(GATEWAY_FIELDS)
        session.commit()

    with tenant_session(context_for(tenant.tenant_id)) as session:
        assert seed_field_config(
            session, tenant.tenant_id, domain_scopes=(GATEWAY_SCOPE,)
        ) == 0


@requires_db
@pytest.mark.db
def test_hiding_a_field_does_not_start_rejecting_writes():
    """``visible`` is a UI preference (§7.3: "UI 可配显隐").

    Letting it gate writes would turn a cosmetic setting into an integration
    outage for whatever external system had been filling the field in.
    """
    from sqlalchemy import select

    from relay.infra.db.models import AiContextFieldConfig

    tenant = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="别的团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with tenant_session(context_for(tenant.tenant_id)) as session:
        row = session.scalars(
            select(AiContextFieldConfig).where(AiContextFieldConfig.field_key == "trace_id")
        ).one()
        row.visible = False
        session.commit()

    with tenant_session(context_for(tenant.tenant_id)) as session:
        assert validate_write(session, {"trace_id": ["t-1"]}) == {"trace_id": ["t-1"]}
