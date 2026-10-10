"""Task complexity, the routing table and the routed provider (design spec 5.3 and 5.4)."""
from __future__ import annotations

import dataclasses
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from engine.ir.complexity import score as score_process
from engine.ir.models import Process

from .provider import (
    TIERS,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderUnavailable,
    Tier,
    mask_sensitive,
)

TaskKind = Literal["analyse", "challenge", "design", "build", "test", "repair", "review", "name"]

TASK_WEIGHTS: dict[str, float] = {
    "analyse": 1.0,
    "challenge": 1.2,
    "design": 1.1,
    "build": 1.3,
    "test": 0.9,
    "repair": 1.3,  # a repair is a build step on a failing test
    "review": 1.0,  # equivalence, security and standards review of generated code
    "name": 0.3,  # naming a domain is a short summarisation
}
HARD_CONSTRUCT_BONUS = 2.0  # build only: Java code or XPath/XSLT 2.0 functions in the process

# Functions that exist only from XPath 2.0 on; their presence means the C# translation
# cannot lean on a simple 1.0 equivalent.
_XPATH2_FUNCTIONS = (
    "tokenize(", "matches(", "replace(", "string-join(", "format-dateTime(", "format-date(",
    "format-time(", "current-dateTime(", "current-date(", "distinct-values(", "index-of(",
    "xs:", "for $", "some $", "every $",
)


def _has_hard_constructs(process: Process) -> bool:
    if any(a.type == "java.invoke" for a in process.activities):
        return True
    if any(a.type == "xslt.transform" for a in process.activities):
        return True
    bindings = [b for a in process.activities for b in a.input_bindings]
    if any(b.language == "xslt" for b in bindings):
        return True
    exprs = [b.expression for b in bindings] + [
        t.expression for t in process.transitions if t.expression
    ]
    return any(fn in e for e in exprs for fn in _XPATH2_FUNCTIONS)


def task_complexity(process: Process | None, task_kind: TaskKind) -> float:
    """Process complexity (0 to 10) times the task weight, plus 2 for build when the process
    holds Java code or XSLT 2.0 functions, clamped to 0 to 10. A task with no process (naming
    domains) scores 0."""
    if process is None:
        return 0.0
    base = process.complexity.score if process.complexity else score_process(process).score
    value = base * TASK_WEIGHTS[task_kind]
    if task_kind == "build" and _has_hard_constructs(process):
        value += HARD_CONSTRUCT_BONUS
    return round(min(max(value, 0.0), 10.0), 4)


@dataclass
class RoutingTable:
    """Inclusive integer score bands per tier. Scores are rounded half up before lookup."""

    bands: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {"strong": (8, 10), "standard": (4, 7), "efficient": (0, 3)}
    )

    def tier_for(self, score: float) -> Tier:
        value = math.floor(score + 0.5)
        for tier in TIERS:
            lo, hi = self.bands.get(tier, (1, 0))
            if lo <= value <= hi:
                return tier
        return "strong" if value > 7 else "efficient"


@dataclass
class ModelCallRecord:
    task_kind: str
    complexity: float
    tier: str
    provider: str
    model: str
    usage: dict[str, int]
    cost: float
    latency: int  # milliseconds
    fell_back: bool
    tags: dict[str, str]
    cost_estimated: bool = False
    error: str | None = None  # set when the call failed and no response was obtained
    primary_error: str | None = None


Recorder = Callable[[ModelCallRecord], Awaitable[None]]


class RoutedProvider:
    """Chooses a model tier from the task's complexity, falls back to the secondary provider on
    ``ProviderError`` (rate limit, outage, missing configuration) and records every call,
    failed ones included. ``sensitive_fields`` extends the masking applied to recorded tags and
    error text."""

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None,
        table: RoutingTable | None = None,
        recorder: Recorder | None = None,
        sensitive_fields: Sequence[str] = (),
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.table = table or RoutingTable()
        self.recorder = recorder
        self.sensitive_fields = tuple(sensitive_fields)

    def _mask(self, text: str) -> str:
        return mask_sensitive(text, self.sensitive_fields)

    @staticmethod
    async def _attempt(provider: LLMProvider, req: LLMRequest, tier: Tier) -> LLMResponse:
        model = provider.models().get(tier, "")
        if not model:
            raise ProviderUnavailable(
                f"{provider.name} has no model or deployment configured for the {tier} tier"
            )
        return await provider.complete(dataclasses.replace(req, model=model))

    async def _record(self, record: ModelCallRecord) -> None:
        if self.recorder is not None:
            await self.recorder(record)

    async def complete_for(
        self,
        task_kind: TaskKind,
        process: Process | None,
        req: LLMRequest,
        *,
        tier: Tier | None = None,
    ) -> LLMResponse:
        """Run ``req`` on the tier the complexity score selects; ``tier`` forces a tier (the
        independent challenger always runs on the strongest)."""
        complexity = task_complexity(process, task_kind)
        chosen: Tier = tier or self.table.tier_for(complexity)
        tags = {k: self._mask(str(v)) for k, v in req.tags.items()}
        started = time.monotonic()

        async def failed(provider: LLMProvider, exc: Exception, fell_back: bool,
                         primary_error: str | None) -> None:
            await self._record(
                ModelCallRecord(
                    task_kind=task_kind,
                    complexity=complexity,
                    tier=chosen,
                    provider=provider.name,
                    model=provider.models().get(chosen, ""),
                    usage={"in": 0, "out": 0, "cached": 0},
                    cost=0.0,
                    latency=int((time.monotonic() - started) * 1000),
                    fell_back=fell_back,
                    tags=tags,
                    error=self._mask(str(exc)),
                    primary_error=primary_error,
                )
            )

        fell_back = False
        primary_error: str | None = None
        try:
            resp = await self._attempt(self.primary, req, chosen)
        except ProviderError as exc:
            if self.fallback is None:
                await failed(self.primary, exc, False, None)
                raise
            primary_error = self._mask(str(exc))
            fell_back = True
            try:
                resp = await self._attempt(self.fallback, req, chosen)
            except ProviderError as exc2:
                await failed(self.fallback, exc2, True, primary_error)
                raise
        await self._record(
            ModelCallRecord(
                task_kind=task_kind,
                complexity=complexity,
                tier=chosen,
                provider=resp.provider,
                model=resp.model,
                usage={"in": resp.usage_in, "out": resp.usage_out, "cached": resp.cached_tokens},
                cost=resp.cost_usd,
                latency=resp.latency_ms,
                fell_back=fell_back,
                tags=tags,
                cost_estimated=resp.cost_estimated,
                primary_error=primary_error,
            )
        )
        return resp
