"""LLMPort — **declared, not used, and no table** (§10).

S1 makes no LLM calls at all. That is worth saying plainly rather than
discovering at the exit review: the MVP's entire AI value sat on BOT-3's
AI-drafted ticket, so S1 should be presented as "the workbench first", not as
"Relay has launched".

``llm_call_record`` is deliberately **not created** in S1 — there is nothing to
record, and it ships with BOT as the single data source for BOT-3 drafting and
INT-10 budget alarms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LlmRequest:
    feature: str
    model: str
    prompt: str
    max_tokens: int = 1024


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float


class LLMPort(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse:
        ...
