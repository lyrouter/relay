"""TA-1 · the ``TelemetryAdapter`` seam. **Interface only — no implementation.**

Say this out loud at the week-2 review: TA has no demoable output in S1, and it
is still un-cuttable. Both are true. The 1 pd buys one CI-enforced architectural
constraint — *no code outside the adapter package may touch a gateway API* —
and without it Phase 2 owes rework in four places (alert-to-ticket, change
attribution, read-only ChatOps, environment snapshot).

The constraint is enforced by ``.importlinter``, not by review, because the team
that operates Relay also builds the gateway: they can change code on both sides,
which makes a direct call near certain otherwise.

Implementations (TA-2…TA-4) stay in TODO.md and live in ``relay.infra.telemetry``
when they arrive. Gateway client libraries may be imported **there and nowhere
else**.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: dt.datetime
    end: dt.datetime


@dataclass(frozen=True, slots=True)
class MetricPoint:
    at: dt.datetime
    value: float
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MetricSeries:
    name: str
    unit: str
    points: tuple[MetricPoint, ...]


@dataclass(frozen=True, slots=True)
class TraceSpan:
    span_id: str
    parent_span_id: str | None
    name: str
    started_at: dt.datetime
    duration_ms: float
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Trace:
    trace_id: str
    root_service: str
    started_at: dt.datetime
    duration_ms: float
    spans: tuple[TraceSpan, ...]


@dataclass(frozen=True, slots=True)
class RequestSample:
    """One sampled gateway request.

    ``prompt`` / ``completion`` are intentionally optional and default to None:
    an adapter must be able to answer without carrying model I/O across the
    seam, since S1 has no DLP.
    """

    request_id: str
    at: dt.datetime
    provider: str
    model: str
    status: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_class: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """A deploy, config change or routing-policy change — the raw material for
    Phase 2 change attribution."""

    change_id: str
    at: dt.datetime
    kind: str
    summary: str
    actor: str | None = None
    target: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    checked_at: dt.datetime
    error_rate: float | None = None
    p95_latency_ms: float | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CostBucket:
    key: str
    cost: float
    currency: str
    tokens: int | None = None


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    window: TimeRange
    group_by: str
    buckets: tuple[CostBucket, ...]
    total: float
    currency: str


@runtime_checkable
class TelemetryAdapter(Protocol):
    """The only legal way for Relay to learn anything from a gateway.

    Every method is read-only by design: Relay observes the gateway, it does not
    drive it. Adding a write method here is a design change, not an increment.
    """

    def query_metrics(
        self, name: str, window: TimeRange, group_by: tuple[str, ...] = ()
    ) -> tuple[MetricSeries, ...]:
        ...

    def get_trace(self, trace_id: str) -> Trace | None:
        ...

    def sample_requests(
        self, window: TimeRange, limit: int = 100, filters: dict[str, str] | None = None
    ) -> tuple[RequestSample, ...]:
        ...

    def list_recent_changes(self, window: TimeRange) -> tuple[ChangeEvent, ...]:
        ...

    def get_provider_health(self) -> tuple[ProviderHealth, ...]:
        ...

    def get_cost_breakdown(self, window: TimeRange, group_by: str = "model") -> CostBreakdown:
        ...
