"""The model provider the API uses, built from ``Settings`` per job or request.

``PROVIDER``:

* ``fake``: :class:`ScriptedFakeProvider`, replies derived from the IR (tests, offline demos);
* ``claude``: ``ClaudeCodeProvider`` (``CLAUDE_CODE_OAUTH_TOKEN``), with Azure OpenAI as the
  fallback when Azure is configured;
* ``azure``: ``AzureOpenAIProvider``;
* ``auto``: Claude when its token is set, else Azure when configured, else no provider.

With no provider the analysis runs code only (static rules, verifier, coverage) and says so:
``ai_available`` is false, ``ai_reason`` names what is missing, and the UI shows
:data:`CODE_ONLY_NOTICE`. Every model call (failed ones included) is persisted to
``model_calls`` with the job, project, process and stage.
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.config import Settings
from api.app.models import ModelCall
from api.app.services.fake_responses import ScriptedFakeProvider
from engine.agents.routing import ModelCallRecord, RoutedProvider, RoutingTable
from engine.ir.models import Process

CODE_ONLY_NOTICE = "code-only analysis: no model configured"
NO_MODEL_REASON = ("no model configured: set CLAUDE_CODE_OAUTH_TOKEN, or AZURE_OPENAI_ENDPOINT and "
                   "AZURE_OPENAI_API_KEY with deployments, or PROVIDER=fake")

# engine tag -> model_calls.stage
STAGE_NAMES = {"analyse": "analyst", "risk": "risk", "challenge": "challenger", "name": "namer",
               "design": "designer", "build": "builder", "review": "reviewer", "repair": "repair",
               "test": "test_engineer"}

LogFn = Callable[[ModelCallRecord], Awaitable[None]]


@dataclass
class ProviderChoice:
    provider: RoutedProvider | None
    name: str  # fake | claude | azure | none
    reason: str | None = None  # why there is no provider

    @property
    def available(self) -> bool:
        return self.provider is not None


def _azure_configured(settings: Settings) -> bool:
    return bool(settings.azure_openai_endpoint and settings.azure_openai_api_key)


def _azure(settings: Settings):
    from engine.agents.azure_openai import AzureOpenAIProvider

    return AzureOpenAIProvider(
        endpoint=settings.azure_openai_endpoint, api_key=settings.azure_openai_api_key,
        deployments={"strong": settings.azure_openai_deployment_strong,
                     "standard": settings.azure_openai_deployment_standard,
                     "efficient": settings.azure_openai_deployment_efficient},
        api_version=settings.azure_openai_api_version or None)


def _claude(settings: Settings):
    from engine.agents.claude_code import ClaudeCodeProvider

    env = {**os.environ, "MODEL_STRONG": settings.model_strong,
           "MODEL_STANDARD": settings.model_standard, "MODEL_EFFICIENT": settings.model_efficient}
    return ClaudeCodeProvider(token=settings.claude_code_oauth_token or None, env=env)


def stage_of(record: ModelCallRecord) -> str:
    if record.tags.get("feature") == "ask":
        return "ask"
    tag = record.tags.get("stage", "")
    return STAGE_NAMES.get(tag, tag or record.task_kind)


def model_call_recorder(sessionmaker: async_sessionmaker[AsyncSession], *, job_id: str | None,
                        project_id: str | None, process_id: str | None, primary_name: str,
                        on_record: LogFn | None = None):
    """A ``RoutedProvider`` recorder that writes one ``model_calls`` row per call."""

    async def record(rec: ModelCallRecord) -> None:
        async with sessionmaker() as s:
            s.add(ModelCall(
                job_id=job_id, project_id=project_id, process_id=process_id, stage=stage_of(rec),
                provider=rec.provider, model=rec.model or "", tier=rec.tier,
                tokens_in=rec.usage.get("in", 0), tokens_out=rec.usage.get("out", 0),
                tokens_cached=rec.usage.get("cached", 0), cost_usd=rec.cost,
                latency_ms=float(rec.latency), ok=rec.error is None, error=rec.error,
                fallback_from=primary_name if rec.fell_back else None))
            await s.commit()
        if on_record is not None:
            await on_record(rec)

    return record


def routing_table(settings: Settings) -> RoutingTable:
    """The routing bands from Settings (``routing_bands``), or the spec defaults."""
    if settings.routing_bands:
        return RoutingTable(bands={k: (int(v[0]), int(v[1])) for k, v in settings.routing_bands.items()})
    return RoutingTable()


def raw_providers(settings: Settings, process: Process | None = None) -> list[tuple[str, Any]]:
    """Every configured provider on its own (no fallback), for the per-provider evaluation."""
    out: list[tuple[str, Any]] = []
    if settings.provider == "fake":
        out.append(("fake", ScriptedFakeProvider(process)))
    if settings.claude_code_oauth_token:
        out.append(("claude", _claude(settings)))
    if _azure_configured(settings):
        out.append(("azure", _azure(settings)))
    return out


def build_provider(settings: Settings, recorder=None, process: Process | None = None) -> ProviderChoice:
    """The routed provider for one job or request, or ``ProviderChoice(None, "none", reason)``."""
    chosen = settings.resolved_provider()
    table = routing_table(settings)
    if chosen == "fake":
        return ProviderChoice(RoutedProvider(ScriptedFakeProvider(process), None, table, recorder=recorder),
                              "fake")
    if chosen == "claude":
        fallback = _azure(settings) if _azure_configured(settings) else None
        return ProviderChoice(RoutedProvider(_claude(settings), fallback, table, recorder=recorder), "claude")
    if chosen == "azure":
        if not _azure_configured(settings):
            return ProviderChoice(None, "none", NO_MODEL_REASON)
        return ProviderChoice(RoutedProvider(_azure(settings), None, table, recorder=recorder), "azure")
    return ProviderChoice(None, "none", NO_MODEL_REASON)


# ------------------------------------------------------------------ per-provider evaluation
class RecordingProvider:
    """Wraps one provider for the evaluation: every call (failed ones too) becomes a ``model_calls``
    row (stage ``eval``), so evaluation cost shows in History like any other model use."""

    def __init__(self, inner: Any, sessionmaker: async_sessionmaker[AsyncSession], *, job_id: str,
                 project_id: str | None, process_id: str | None) -> None:
        self.inner = inner
        self.name = inner.name
        self.sessionmaker = sessionmaker
        self.job_id, self.project_id, self.process_id = job_id, project_id, process_id

    def models(self):
        return self.inner.models()

    def _tier(self, model: str) -> str | None:
        return next((t for t, m in self.inner.models().items() if m == model), None)

    async def complete(self, req):
        try:
            resp = await self.inner.complete(req)
        except Exception as exc:
            await self._write(model=req.model, ok=False, error=str(exc)[:2000])
            raise
        await self._write(model=resp.model or req.model, tokens_in=resp.usage_in, tokens_out=resp.usage_out,
                          tokens_cached=resp.cached_tokens, cost=resp.cost_usd, latency=resp.latency_ms)
        return resp

    async def _write(self, *, model: str, ok: bool = True, error: str | None = None, tokens_in: int = 0,
                     tokens_out: int = 0, tokens_cached: int = 0, cost: float = 0.0, latency: float = 0.0) -> None:
        async with self.sessionmaker() as s:
            s.add(ModelCall(job_id=self.job_id, project_id=self.project_id, process_id=self.process_id,
                            stage="eval", provider=self.name, model=model or "", tier=self._tier(model),
                            tokens_in=tokens_in, tokens_out=tokens_out, tokens_cached=tokens_cached,
                            cost_usd=cost, latency_ms=float(latency), ok=ok, error=error))
            await s.commit()


def eval_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per provider, side by side: citation-verified rate, coverage, challenger agreement, rules per
    workflow, latency and cost (counts by the verifier and coverage code, not by the model)."""
    out = []
    for name in dict.fromkeys(r["provider"] for r in rows):
        mine = [r for r in rows if r["provider"] == name]
        ok = [r for r in mine if not r.get("error")]
        cit_v, cit_t = sum(r["citations_verified"] for r in ok), sum(r["citations_total"] for r in ok)
        cov, cov_t = sum(r["coverage"] for r in ok), sum(r["coverage_total"] for r in ok)
        rules = sum(r["rules"] for r in ok)
        out.append({"provider": name, "workflows": len(mine), "errors": len(mine) - len(ok),
                    "citation_verified_rate": cit_v / cit_t if cit_t else None,
                    "coverage_rate": cov / cov_t if cov_t else None,
                    "challenger_agreement": (sum(r["challenger_agree"] for r in ok) / rules) if rules else None,
                    "rules_per_workflow": rules / len(ok) if ok else None,
                    "latency_ms": sum(r["latency_ms"] for r in mine),
                    "cost_usd": round(sum(r["cost_usd"] for r in mine), 6)})
    return out


def eval_job(eval_id: str, process_ids: list[str], settings: Settings):
    from api.app.jobs.runner import JobContext

    async def run(ctx: JobContext) -> dict[str, Any]:
        from dataclasses import asdict

        from api.app.models import Eval
        from api.app.models import Process as ProcessRow
        from api.app.services.context import engine_app, engine_process
        from engine.agents.evals import run_provider_eval

        async with ctx.sessionmaker() as s:
            ev = await s.get(Eval, eval_id)
            ev.status = "running"
            await s.commit()
        rows: list[dict[str, Any]] = []
        try:
            for pid in process_ids:
                async with ctx.sessionmaker() as s:
                    row = await s.get(ProcessRow, pid)
                    if row is None:
                        await ctx.log(f"process {pid} not found; skipped")
                        continue
                    app = await engine_app(s, row)
                    proc = engine_process(app, row)
                    project_id = row.project_id
                providers = [RecordingProvider(p, ctx.sessionmaker, job_id=ctx.job_id, project_id=project_id,
                                               process_id=pid) for _, p in raw_providers(settings, proc)]
                if not providers:
                    raise RuntimeError(NO_MODEL_REASON)
                await ctx.stage(proc.name)
                out = await run_provider_eval([proc], providers, app.contracts, table=routing_table(settings))
                for r in out:
                    d = {**asdict(r), "process_id": pid, "ir_process_id": r.process_id, "process_name": proc.name}
                    rows.append(d)
                    await ctx.log(f"{r.provider} on {proc.name}: citations {r.citations_verified} of "
                                  f"{r.citations_total}, coverage {r.coverage} of {r.coverage_total}, "
                                  f"challenger agreed {r.challenger_agree} of {r.rules}, {r.latency_ms} ms, "
                                  f"${r.cost_usd:.4f}" + (f"; error: {r.error}" if r.error else ""), tag="ai")
        except Exception:
            async with ctx.sessionmaker() as s:
                ev = await s.get(Eval, eval_id)
                ev.status = "failed"
                ev.results = {"rows": rows, "summary": eval_summary(rows)}
                await s.commit()
            raise
        result = {"rows": rows, "summary": eval_summary(rows)}
        async with ctx.sessionmaker() as s:
            ev = await s.get(Eval, eval_id)
            ev.status = "done"
            ev.results = result
            await s.commit()
        return {"eval_id": eval_id, "rows": len(rows), "summary": result["summary"]}

    return run
