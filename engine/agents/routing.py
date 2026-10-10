"""Task complexity, the routing table and the routed provider (design spec 5.3 and 5.4)."""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Awaitable, Callable
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
    Tier,
    mask_sensitive,
)

TaskKind = Literal["analyse", "challenge", "design", "build", "test", "repair", "name"]

TASK_WEIGHTS: dict[str, float] = {
    "analyse": 1.0,
    "challenge": 1.2,
    "design": 1.1,
    "build": 1.3,
    "test": 0.9,
    "repair": 1.3,  # a repair is a build step on a failing test
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
    cost_estimated: bool = True
    primary_error: str | None = None


Recorder = Callable[[ModelCallRecord], Awaitable[None]]


class RoutedProvider:
    """Chooses a model tier from the task's complexity, falls back to the secondary provider on
    ``ProviderError`` (rate limit, outage, missing configuration) and records every call."""

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None,
        table: RoutingTable | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.table = table or RoutingTable()
        self.recorder = recorder

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
        fell_back = False
        primary_error: str | None = None
        try:
            resp = await self.primary.complete(
                dataclasses.replace(req, model=self.primary.models()[chosen])
            )
        except ProviderError as exc:
            if self.fallback is None:
                raise
            primary_error = str(exc)
            fell_back = True
            resp = await self.fallback.complete(
                dataclasses.replace(req, model=self.fallback.models()[chosen])
            )
        if self.recorder is not None:
            await self.recorder(
                ModelCallRecord(
                    task_kind=task_kind,
                    complexity=complexity,
                    tier=chosen,
                    provider=resp.provider,
                    model=resp.model,
                    usage={
                        "in": resp.usage_in,
                        "out": resp.usage_out,
                        "cached": resp.cached_tokens,
                    },
                    cost=resp.cost_usd,
                    latency=resp.latency_ms,
                    fell_back=fell_back,
                    tags={k: mask_sensitive(str(v)) for k, v in req.tags.items()},
                    cost_estimated=resp.cost_estimated,
                    primary_error=mask_sensitive(primary_error) if primary_error else None,
                )
            )
        return resp
