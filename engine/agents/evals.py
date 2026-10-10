"""Per-provider evaluation: run the understanding pipeline on chosen processes with each
provider and report citation-verified rate, coverage, challenger agreement, rules, latency
and cost side by side (design spec section 7, Settings)."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from engine.analysis.models import Rule
from engine.ir.models import Contract, Process

from .analyst import analyse
from .challenger import challenge
from .jsonout import JsonOutputError
from .provider import LLMProvider, MemoryItem, ProviderError
from .routing import ModelCallRecord, RoutedProvider, RoutingTable

VerifyFn = Callable[[Process, list[Rule]], Any]  # returns an object with .verified and .total
CoverageFn = Callable[[Process, list[Rule], dict[str, str]], Any]  # .covered and .total


@dataclass
class EvalRow:
    provider: str
    model: str
    process_id: str
    citations_verified: int
    citations_total: int
    coverage: int
    coverage_total: int
    challenger_agree: int  # rules the challenger agreed with
    rules: int
    latency_ms: int
    cost_usd: float
    error: str | None = None


def _default_verify() -> VerifyFn:
    from engine.analysis.verifier import verify_citations  # Task 8

    return verify_citations


def _default_coverage() -> CoverageFn:
    from engine.analysis.coverage import coverage_matrix  # Task 8

    return coverage_matrix


async def run_provider_eval(
    processes: Sequence[Process],
    providers: Sequence[LLMProvider],
    contracts: list[Contract],
    *,
    verifier: VerifyFn | None = None,
    coverage_fn: CoverageFn | None = None,
    memory_items: list[MemoryItem] | None = None,
    table: RoutingTable | None = None,
) -> list[EvalRow]:
    """One row per provider and process. Each provider runs alone (no fallback) through the same
    routing table, so the row shows what that provider does by itself. Counting is done by the
    verifier and coverage functions, not by the model. A provider failure becomes a row with
    ``error`` set and zero counts, never an exception."""
    verify = verifier or _default_verify()
    cover = coverage_fn or _default_coverage()
    rows: list[EvalRow] = []
    for provider in providers:
        for process in processes:
            records: list[ModelCallRecord] = []

            async def record(r: ModelCallRecord, _sink: list[ModelCallRecord] = records) -> None:
                _sink.append(r)

            routed = RoutedProvider(provider, None, table or RoutingTable(), record)
            try:
                out = await analyse(process, contracts, memory_items or [], routed, verifier=None)
                verification = verify(process, out.rules)
                coverage = cover(process, out.rules, out.technical)
                challenged = await challenge(process, out.rules, routed, contracts)
            except (ProviderError, JsonOutputError) as exc:
                rows.append(_failed_row(provider, process, records, str(exc)))
                continue
            rows.append(
                EvalRow(
                    provider=provider.name,
                    model=records[0].model if records else "",
                    process_id=process.id,
                    citations_verified=int(verification.verified),
                    citations_total=int(verification.total),
                    coverage=int(coverage.covered),
                    coverage_total=int(coverage.total),
                    challenger_agree=sum(
                        1 for r in challenged if r.challenge and r.challenge.verdict == "agree"
                    ),
                    rules=len(out.rules),
                    latency_ms=sum(r.latency for r in records),
                    cost_usd=round(sum(r.cost for r in records), 6),
                )
            )
    return rows


def _failed_row(
    provider: LLMProvider, process: Process, records: list[ModelCallRecord], error: str
) -> EvalRow:
    return EvalRow(
        provider=provider.name,
        model=records[0].model if records else "",
        process_id=process.id,
        citations_verified=0,
        citations_total=0,
        coverage=0,
        coverage_total=0,
        challenger_agree=0,
        rules=0,
        latency_ms=sum(r.latency for r in records),
        cost_usd=round(sum(r.cost for r in records), 6),
        error=error,
    )
