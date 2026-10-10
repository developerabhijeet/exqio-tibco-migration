"""Settings screen: providers (presence, never values), models per tier and stage, routing bands,
toolchains, Langfuse. Edits are stored in ``settings`` and applied to the running API's ``Settings``
(the next job reads them); keys and tokens stay in the environment.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.models import Setting
from engine.agents.routing import TASK_WEIGHTS, RoutingTable

TIERS = ("strong", "standard", "efficient")
# Pipeline stage -> (engine task kind, how its tier is chosen)
STAGES: list[tuple[str, str, str]] = [
    ("analyst", "analyse", "by task complexity"),
    ("risk", "analyse", "by task complexity"),
    ("challenger", "challenge", "strong (always: independent second opinion)"),
    ("namer", "name", "by task complexity"),
    ("designer", "design", "by task complexity"),
    ("builder", "build", "by task complexity"),
    ("reviewer", "challenge", "by task complexity"),
    ("repair", "repair", "by task complexity"),
    ("ask", "analyse", "by task complexity"),
]
FALLBACK_NOTE = ("Auto mode: Claude first; on an error or a rate limit the call goes to Azure OpenAI when "
                 "it is configured, and the switch is recorded on the model call (fallback_from).")


def detect_toolchains() -> dict[str, Any]:
    from engine.dotnet_gen.runner import _toolchain_detect

    return _toolchain_detect()


async def load_stored(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(Setting))).scalars().all()
    return {r.key: r.value for r in rows}


def apply_stored(settings: Settings, stored: dict[str, Any]) -> None:
    """Copy stored edits onto the live ``Settings`` (models per tier, routing bands, Langfuse switch)."""
    models = stored.get("models") or {}
    for tier in TIERS:
        if models.get(tier):
            setattr(settings, f"model_{tier}", str(models[tier]))
    bands = stored.get("routing_bands")
    if bands:
        settings.routing_bands = {k: (int(v[0]), int(v[1])) for k, v in bands.items()}
    if "langfuse_enabled" in stored:
        settings.langfuse_enabled = bool(stored["langfuse_enabled"])


def validate_bands(bands: dict[str, Any]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for tier in TIERS:
        if tier not in bands:
            raise ValueError(f"routing band for {tier} is missing")
        lo, hi = (int(x) for x in bands[tier])
        if not (0 <= lo <= hi <= 10):
            raise ValueError(f"routing band for {tier} must satisfy 0 <= low <= high <= 10")
        out[tier] = (lo, hi)
    covered = sorted(v for lo, hi in out.values() for v in range(lo, hi + 1))
    if covered != list(range(11)):
        raise ValueError("routing bands must cover every score 0 to 10 exactly once")
    return out


def settings_view(settings: Settings, toolchains: dict[str, Any]) -> dict[str, Any]:
    azure = bool(settings.azure_openai_endpoint and settings.azure_openai_api_key)
    bands = settings.routing_bands or RoutingTable().bands
    return {
        "provider": {"choice": settings.provider, "active": settings.resolved_provider()},
        "providers": [
            {"name": "claude", "role": "primary", "configured": bool(settings.claude_code_oauth_token),
             "env": ["CLAUDE_CODE_OAUTH_TOKEN"]},
            {"name": "azure", "role": "fallback", "configured": azure,
             "env": ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT_STRONG",
                     "AZURE_OPENAI_DEPLOYMENT_STANDARD", "AZURE_OPENAI_DEPLOYMENT_EFFICIENT"],
             "endpoint_set": bool(settings.azure_openai_endpoint),
             "key_set": bool(settings.azure_openai_api_key),
             "deployments_set": {t: bool(getattr(settings, f"azure_openai_deployment_{t}")) for t in TIERS}},
            {"name": "fake", "role": "offline scripted replies (tests and demos)",
             "configured": settings.provider == "fake", "env": ["PROVIDER=fake"]},
        ],
        "models": {
            "tiers": {t: {"claude": getattr(settings, f"model_{t}"),
                          "azure_deployment_set": bool(getattr(settings, f"azure_openai_deployment_{t}"))}
                      for t in TIERS},
            "stages": [{"stage": s, "task_kind": k, "weight": TASK_WEIGHTS.get(k), "tier": how}
                       for s, k, how in STAGES],
        },
        "routing": {"bands": {t: list(bands.get(t, (0, 0))) for t in TIERS}, "fallback": FALLBACK_NOTE,
                    "complexity": "process complexity (0 to 10) times the task weight, plus 2 for build when "
                                  "Java or XSLT 2.0 functions are present, clamped 0 to 10"},
        "toolchains": toolchains,
        "langfuse": {"enabled": settings.langfuse_enabled, "host": settings.langfuse_host or None,
                     "keys_configured": bool(settings.langfuse_public_key and settings.langfuse_secret_key)},
        "app_secret_configured": bool(settings.app_secret),
        "scenario_database": "override (SCENARIO_DATABASE_URL)" if settings.scenario_database_url
        else "one per project: exiqo_scn_<project>_test",
    }


async def update(session: AsyncSession, settings: Settings, body: dict[str, Any], user: str) -> None:
    changes: dict[str, Any] = {}
    if body.get("models") is not None:
        models = {t: str(v).strip() for t, v in body["models"].items() if t in TIERS and str(v or "").strip()}
        if models:
            changes["models"] = models
    if body.get("routing_bands") is not None:
        changes["routing_bands"] = {k: list(v) for k, v in validate_bands(body["routing_bands"]).items()}
    if body.get("langfuse_enabled") is not None:
        changes["langfuse_enabled"] = bool(body["langfuse_enabled"])
    for key, value in changes.items():
        row = await session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value, updated_by=user))
        else:
            row.value = {**row.value, **value} if isinstance(value, dict) and isinstance(row.value, dict) \
                and key == "models" else value
            row.updated_by = user
    await session.commit()
    apply_stored(settings, await load_stored(session))
