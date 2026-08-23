"""TKT-2 · seeding and enforcing the AI-context field config.

The registry (which fields exist, what they mean, which are gateway-only) is
domain knowledge and lives in :mod:`relay.domain.ai_context`. This module is the
part that touches rows: seeding a tenant's config at bootstrap, and validating a
write against whatever that tenant actually has.

Why the gate is data and not a code path: a tenant with no row for
``routing_policy`` cannot write it, because validation is built from its own
rows. There is no branch to forget and no flag to get backwards — adding the
gateway fields to a second tenant is a deliberate act with an audit row, not a
config change nobody reviews.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from relay.domain.ai_context import (
    ALL_FIELDS,
    AiContextField,
    validate,
)
from relay.infra.db.models import AiContextFieldConfig


def seed_field_config(
    session, tenant_id: uuid.UUID, *, domain_scopes: tuple[str, ...] = ()
) -> int:
    """Give a tenant its field config. Returns how many rows were added.

    Idempotent by ``(tenant_id, field_key)``, so a re-run of the bootstrap adds
    nothing — and a tenant that later gains a domain scope gets only the new
    rows rather than a reset of the visibility somebody has been tuning.

    Runs on whatever session it is handed: bootstrap uses the system connection
    (the tenant does not exist yet from RLS's point of view), everything else
    uses a tenant session.
    """
    existing = set(
        session.scalars(
            select(AiContextFieldConfig.field_key).where(
                AiContextFieldConfig.tenant_id == tenant_id
            )
        )
    )
    added = 0
    for field in ALL_FIELDS:
        if field.key in existing:
            continue
        if field.domain_scope is not None and field.domain_scope not in domain_scopes:
            continue
        session.add(
            AiContextFieldConfig(
                tenant_id=tenant_id,
                field_key=field.key,
                label=field.label,
                type=field.type,
                visible=True,
                domain_scope=field.domain_scope,
            )
        )
        added += 1
    session.flush()
    return added


def configured_fields(session) -> tuple[AiContextField, ...]:
    """This tenant's fields, read under RLS.

    Note that ``visible`` is **not** filtered here. Visibility is a UI
    preference (§7.3: "UI 可配显隐"); hiding a field must not silently start
    rejecting writes from an external system that has been filling it in, which
    would turn a cosmetic setting into an integration outage.
    """
    rows = session.scalars(
        select(AiContextFieldConfig).order_by(AiContextFieldConfig.field_key)
    ).all()
    return tuple(
        AiContextField(
            key=row.field_key, label=row.label, type=row.type, domain_scope=row.domain_scope
        )
        for row in rows
    )


def validate_write(session, values: dict | None) -> dict:
    """Validate ``ai_context`` against this tenant's config. Never arbitrary JSON."""
    if not values:
        return {}
    return validate(values, configured_fields(session))
