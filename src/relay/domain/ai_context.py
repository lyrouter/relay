"""TKT-2 · the AI-context field registry and its write validation (design §7.3).

**The only reason this exists in S1 is to avoid a later migration and index
rebuild.** Not usability — there is no automatic data source, so every value is
typed in by a person or written by an external system through the API. Say that
at review; a defence built on S1 usefulness will not survive contact.

The judgment that keeps the generic set honest, from §7.3: the first team also
*builds* the gateway, so every request they make looks generic. The test before
promoting a field is **could a team with no gateway of its own fill this in?** If
not, it is ``domain_scope``-gated and stays out of the generic set. That is what
separates ``error_class`` (any team running anything has error classes) from
``routing_policy`` (meaningless without a gateway to route).

Writes are validated against the tenant's ``ai_context_field_config`` rows —
**not stored as arbitrary JSON** — because §8 lets the public API write these
fields. A tenant without a row for a gated field cannot write it at all, so the
gate is data rather than a code path someone can forget to take.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from relay.domain.enums import AiContextFieldType

#: The gate value for gateway-only fields. One constant so a typo cannot
#: silently promote a field into the generic set.
GATEWAY_SCOPE = "gateway"


@dataclass(frozen=True, slots=True)
class AiContextField:
    key: str
    label: str
    type: AiContextFieldType
    #: None = generic, enabled for every tenant.
    domain_scope: str | None = None


#: Generic AI-Ops fields: default-on for every tenant (§7.3, row 1).
GENERIC_FIELDS: tuple[AiContextField, ...] = (
    AiContextField("trace_id", "Trace ID", AiContextFieldType.STRING_LIST),
    AiContextField("provider", "模型厂商", AiContextFieldType.STRING_LIST),
    AiContextField("model", "模型", AiContextFieldType.STRING_LIST),
    AiContextField("prompt_version", "Prompt 版本", AiContextFieldType.STRING),
    AiContextField("deployment", "部署环境", AiContextFieldType.STRING),
    AiContextField("error_class", "错误类型", AiContextFieldType.STRING),
    AiContextField("eval_run", "评测批次", AiContextFieldType.STRING),
    AiContextField("token_cost", "Token 成本", AiContextFieldType.NUMBER),
    AiContextField("blast_radius", "影响面", AiContextFieldType.STRING),
    #: The *subject* tenant of the incident, not Relay's own tenant boundary —
    #: a gateway operator triaging "which of my customers saw this". Naming is
    #: unfortunate and it is §7.3's word, so it stays.
    AiContextField("tenant", "受影响租户", AiContextFieldType.STRING_LIST),
)

#: Gateway-only (§7.3, row 2): on for the first tenant, gated for everyone else.
GATEWAY_FIELDS: tuple[AiContextField, ...] = (
    AiContextField("gateway_version", "网关版本", AiContextFieldType.STRING, GATEWAY_SCOPE),
    AiContextField("routing_policy", "路由策略", AiContextFieldType.STRING, GATEWAY_SCOPE),
)

ALL_FIELDS: tuple[AiContextField, ...] = GENERIC_FIELDS + GATEWAY_FIELDS

FIELDS_BY_KEY: dict[str, AiContextField] = {field.key: field for field in ALL_FIELDS}


_PYTHON_TYPES: dict[AiContextFieldType, Any] = {
    AiContextFieldType.STRING: str,
    AiContextFieldType.STRING_LIST: list[str],
    AiContextFieldType.NUMBER: float,
    AiContextFieldType.BOOLEAN: bool,
}


class InvalidAiContext(ValueError):
    """Names the offending key and what was expected, because the caller may be
    a script whose author is reading a log line rather than a form."""


def build_model(fields: tuple[AiContextField, ...]) -> type[BaseModel]:
    """A Pydantic model for exactly the fields this tenant has configured.

    ``extra="forbid"`` is the load-bearing part: an unconfigured key is an error,
    not a passenger. Otherwise ``ai_context`` degrades into the arbitrary JSON
    column §7.3 explicitly refuses, and the migration this whole task exists to
    avoid becomes necessary anyway — the values would already be in production
    under keys nobody declared.
    """
    definitions: dict[str, Any] = {
        field.key: (_PYTHON_TYPES[field.type] | None, None) for field in fields
    }
    return create_model(  # type: ignore[call-overload]
        "AiContextWrite",
        __config__=ConfigDict(extra="forbid", strict=False),
        **definitions,
    )


def validate(values: dict, fields: tuple[AiContextField, ...]) -> dict:
    """Validate a write and return it normalised, dropping unset keys.

    Unset keys are dropped rather than stored as null so that ``ai_context``
    holds what somebody actually filled in. A column of ten nulls per ticket is
    the same absence, stored ten times, and it makes "which fields does this
    tenant use?" unanswerable from the data.
    """
    if not values:
        return {}
    try:
        model = build_model(fields).model_validate(values)
    except ValidationError as exc:
        raise InvalidAiContext(_explain(exc, fields)) from exc
    return {key: value for key, value in model.model_dump().items() if value is not None}


def _explain(exc: ValidationError, fields: tuple[AiContextField, ...]) -> str:
    configured = "、".join(field.key for field in fields) or "无"
    problems = []
    for error in exc.errors():
        key = ".".join(str(part) for part in error["loc"]) or "?"
        if error["type"] == "extra_forbidden":
            problems.append(f"字段 {key} 未在本租户启用（可用字段：{configured}）")
        else:
            problems.append(f"字段 {key}：{error['msg']}")
    return "; ".join(problems)
