"""Settings screen: providers (presence, never values), models per tier and stage, routing bands,
toolchains, Langfuse. Edits are stored in ``settings`` and applied to the running API's ``Settings``
(the next job reads them); keys and tokens stay in the environment.
"""
from __future__ import annotations

import logging
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.models import Setting
from api.app.services.infra import make_fernet
from engine.agents.routing import TASK_WEIGHTS, RoutingTable

log = logging.getLogger(__name__)

TIERS = ("strong", "standard", "efficient")
# Pipeline stage -> (engine task kind, how its tier is chosen)
STAGES: list[tuple[str, str, str]] = [
    ("analyst", "analyse", "by task complexity"),
    ("risk", "analyse", "by task complexity"),
    ("challenger", "challenge", "strong (always: independent second opinion)"),
    ("namer", "name", "by task complexity"),
    ("designer", "design", "by task complexity"),
    ("test_engineer", "test", "by task complexity"),
    ("builder", "build", "by task complexity"),
    ("reviewer", "challenge", "by task complexity (stage tag review)"),
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


AZURE_KEY = "azure_openai"


def apply_stored(settings: Settings, stored: dict[str, Any]) -> None:
    """Copy stored edits onto the live ``Settings`` (models per tier, routing bands, Langfuse switch,
    and an Azure OpenAI endpoint and key brought by the user).

    Bringing your own key is only this: the stored values are written onto the same ``Settings``
    fields the environment would have filled, so every provider path downstream is unchanged and
    cannot tell where the credentials came from. Stored values win over the environment, because
    someone typing a key into the screen means to use it."""
    models = stored.get("models") or {}
    for tier in TIERS:
        if models.get(tier):
            setattr(settings, f"model_{tier}", str(models[tier]))
    bands = stored.get("routing_bands")
    if bands:
        settings.routing_bands = {k: (int(v[0]), int(v[1])) for k, v in bands.items()}
    if "langfuse_enabled" in stored:
        settings.langfuse_enabled = bool(stored["langfuse_enabled"])
    azure = stored.get(AZURE_KEY) or {}
    # A stored block is authoritative for the fields it carries, empty ones included: clearing the
    # key in the screen has to take it out of the live settings too, not just out of the row.
    if "endpoint" in azure:
        settings.azure_openai_endpoint = str(azure["endpoint"] or "")
    if "deployments" in azure:
        for tier in TIERS:
            name = (azure["deployments"] or {}).get(tier)
            setattr(settings, f"azure_openai_deployment_{tier}", str(name or ""))
    if "api_key_enc" in azure:
        enc = str(azure["api_key_enc"] or "")
        if not enc:
            settings.azure_openai_api_key = ""
        else:
            try:
                fernet, _ = make_fernet(settings)
                settings.azure_openai_api_key = fernet.decrypt(enc.encode()).decode()
            except (InvalidToken, ValueError):
                # stored under a different APP_SECRET; the screen says so rather than guessing
                log.warning("the stored Azure OpenAI key cannot be decrypted with this APP_SECRET")


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


def settings_view(settings: Settings, toolchains: dict[str, Any],
                  stored_azure: dict[str, Any] | None = None) -> dict[str, Any]:
    azure = bool(settings.azure_openai_endpoint and settings.azure_openai_api_key)
    bands = settings.routing_bands or RoutingTable().bands
    return {
        "provider": {"choice": settings.provider, "active": settings.resolved_provider()},
        "providers": [
            {"name": "claude", "role": "primary", "configured": bool(settings.claude_code_oauth_token),
             "env": ["CLAUDE_CODE_OAUTH_TOKEN"]},
            {"name": "azure", "role": "Azure OpenAI", "configured": azure,
             "env": ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT_STRONG",
                     "AZURE_OPENAI_DEPLOYMENT_STANDARD", "AZURE_OPENAI_DEPLOYMENT_EFFICIENT"],
             "endpoint_set": bool(settings.azure_openai_endpoint),
             "key_set": bool(settings.azure_openai_api_key),
             "deployments_set": {t: bool(getattr(settings, f"azure_openai_deployment_{t}")) for t in TIERS}},
            {"name": "fake", "role": "built-in replies, no model call",
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
        # Bring your own key: what the screen needs to show the form and say where the credentials
        # came from. The key itself is never returned, only whether one is stored.
        "azure_openai": {
            "endpoint": settings.azure_openai_endpoint or "",
            "key_set": bool(settings.azure_openai_api_key),
            "deployments": {t: getattr(settings, f"azure_openai_deployment_{t}") or "" for t in TIERS},
            "source": "stored" if (stored_azure or {}).get("api_key_enc") else
                      ("environment" if settings.azure_openai_api_key else "not configured"),
            "app_secret_configured": bool(settings.app_secret),
        },
        "toolchains": toolchains,
        "langfuse": {"enabled": settings.langfuse_enabled, "host": settings.langfuse_host or None,
                     "keys_configured": bool(settings.langfuse_public_key and settings.langfuse_secret_key)},
        "app_secret_configured": bool(settings.app_secret),
        "scenario_database": "override (SCENARIO_DATABASE_URL)" if settings.scenario_database_url
        else "one per project: exiqo_scn_<project>_test",
    }



def _azure_change(session: AsyncSession, settings: Settings, azure: dict[str, Any]) -> dict[str, Any]:
    """The stored Azure OpenAI block. The key is encrypted here and never stored or returned in the
    clear; an empty string clears the whole block, which is how a user takes their key back."""
    out: dict[str, Any] = {}
    if azure.get("clear"):
        return {"endpoint": "", "deployments": {}, "api_key_enc": ""}
    if azure.get("endpoint") is not None:
        endpoint = str(azure["endpoint"]).strip()
        if endpoint and not endpoint.startswith(("http://", "https://")):
            raise ValueError("the Azure OpenAI endpoint must be an http(s) URL")
        out["endpoint"] = endpoint
    if azure.get("deployments") is not None:
        out["deployments"] = {t: str(v).strip() for t, v in azure["deployments"].items()
                              if t in TIERS and str(v or "").strip()}
    if azure.get("api_key") is not None:
        key = str(azure["api_key"]).strip()
        if key and not settings.app_secret:
            # Without APP_SECRET make_fernet invents a key per call, so anything encrypted now could
            # never be read back. Refusing is the honest answer; storing it would look like success
            # and fail at the next restart.
            raise ValueError("set APP_SECRET before storing an Azure OpenAI key here: without it the "
                             "key cannot be decrypted again. Until then use AZURE_OPENAI_API_KEY in "
                             "the environment.")
        fernet, _ = make_fernet(settings)
        out["api_key_enc"] = fernet.encrypt(key.encode()).decode() if key else ""
    return out


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
    if body.get("azure") is not None:
        changes[AZURE_KEY] = _azure_change(session, settings, body["azure"])
    for key, value in changes.items():
        row = await session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value, updated_by=user))
        else:
            merge = key in ("models", AZURE_KEY)
            row.value = {**row.value, **value} if merge and isinstance(value, dict) \
                and isinstance(row.value, dict) else value
            row.updated_by = user
    await session.commit()
    apply_stored(settings, await load_stored(session))
