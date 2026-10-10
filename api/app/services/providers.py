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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.config import Settings
from api.app.models import ModelCall
from api.app.services.fake_responses import ScriptedFakeProvider
from engine.agents.routing import ModelCallRecord, RoutedProvider
from engine.ir.models import Process

CODE_ONLY_NOTICE = "code-only analysis: no model configured"
NO_MODEL_REASON = ("no model configured: set CLAUDE_CODE_OAUTH_TOKEN, or AZURE_OPENAI_ENDPOINT and "
                   "AZURE_OPENAI_API_KEY with deployments, or PROVIDER=fake")

# engine tag -> model_calls.stage
STAGE_NAMES = {"analyse": "analyst", "risk": "risk", "challenge": "challenger", "name": "namer"}

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


def build_provider(settings: Settings, recorder=None, process: Process | None = None) -> ProviderChoice:
    """The routed provider for one job or request, or ``ProviderChoice(None, "none", reason)``."""
    chosen = settings.resolved_provider()
    if chosen == "fake":
        return ProviderChoice(RoutedProvider(ScriptedFakeProvider(process), None, recorder=recorder), "fake")
    if chosen == "claude":
        fallback = _azure(settings) if _azure_configured(settings) else None
        return ProviderChoice(RoutedProvider(_claude(settings), fallback, recorder=recorder), "claude")
    if chosen == "azure":
        if not _azure_configured(settings):
            return ProviderChoice(None, "none", NO_MODEL_REASON)
        return ProviderChoice(RoutedProvider(_azure(settings), None, recorder=recorder), "azure")
    return ProviderChoice(None, "none", NO_MODEL_REASON)
