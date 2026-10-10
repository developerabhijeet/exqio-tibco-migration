"""Azure OpenAI (or Azure AI Foundry) as the fallback provider. Keys from the environment only."""
from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from typing import Any

import openai

from .jsonout import json_instruction
from .provider import (
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    Tier,
    estimate_cost,
)

DEFAULT_API_VERSION = "2024-10-21"
API_VERSION = DEFAULT_API_VERSION
API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
# o-series reasoning deployments (o1, o3, o4-mini) and gpt-5 deployments reject ``max_tokens``
# and require ``max_completion_tokens``. The check is on the deployment NAME, which the operator
# chooses, so a deployment with an unrelated name backed by such a model needs
# ``use_completion_tokens=True``.
_COMPLETION_TOKENS_NAME = re.compile(r"^o\d|gpt-?5", re.IGNORECASE)


def uses_completion_tokens(deployment: str) -> bool:
    return bool(_COMPLETION_TOKENS_NAME.search(deployment or ""))

_DEPLOYMENT_ENV = {
    "strong": "AZURE_OPENAI_DEPLOYMENT_STRONG",
    "standard": "AZURE_OPENAI_DEPLOYMENT_STANDARD",
    "efficient": "AZURE_OPENAI_DEPLOYMENT_EFFICIENT",
}


class AzureOpenAIProvider:
    name = "azure-openai"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployments: Mapping[str, str] | None = None,
        client: Any | None = None,
        env: Mapping[str, str] | None = None,
        api_version: str | None = None,
        use_completion_tokens: bool | None = None,
    ) -> None:
        e: Mapping[str, str] = os.environ if env is None else env
        self.endpoint = endpoint or e.get("AZURE_OPENAI_ENDPOINT") or None
        self.api_key = api_key or e.get("AZURE_OPENAI_API_KEY") or None
        self.api_version = api_version or e.get(API_VERSION_ENV) or DEFAULT_API_VERSION
        self._use_completion_tokens = use_completion_tokens
        if deployments is None:
            deployments = {t: e.get(var, "") for t, var in _DEPLOYMENT_ENV.items()}
        self._deployments: dict[Tier, str] = {
            t: deployments.get(t, "") for t in ("strong", "standard", "efficient")  # type: ignore[misc]
        }
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self.endpoint and self.api_key and any(self._deployments.values()))

    def models(self) -> dict[Tier, str]:
        return dict(self._deployments)

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.available:
                raise ProviderUnavailable("Azure OpenAI endpoint, key or deployments are not set")
            self._client = openai.AsyncAzureOpenAI(
                azure_endpoint=self.endpoint, api_key=self.api_key, api_version=self.api_version
            )
        return self._client

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if self._client is None and not self.available:
            raise ProviderUnavailable("Azure OpenAI endpoint, key or deployments are not set")
        client = self._get_client()
        system = req.system + (json_instruction(req.json_schema) if req.json_schema else "")
        kwargs: dict[str, Any] = {
            "model": req.model,
            "messages": [{"role": "system", "content": system}, *req.messages],
        }
        override = self._use_completion_tokens
        if override if override is not None else uses_completion_tokens(req.model):
            kwargs["max_completion_tokens"] = req.max_tokens
        else:
            kwargs["max_tokens"] = req.max_tokens
        if req.json_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        try:
            resp = await client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise RateLimited(f"azure-openai: {exc}") from exc
        except openai.OpenAIError as exc:
            raise ProviderError(f"azure-openai: {exc}") from exc
        usage = resp.usage
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
        details = getattr(usage, "prompt_tokens_details", None)
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        model = getattr(resp, "model", None) or req.model
        cost = estimate_cost(model, tokens_in, tokens_out, cached)
        if cost is None:  # the reported model is not priced; try the deployment name
            cost = estimate_cost(req.model, tokens_in, tokens_out, cached)
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            usage_in=tokens_in,
            usage_out=tokens_out,
            cached_tokens=cached,
            cost_usd=cost if cost is not None else 0.0,
            latency_ms=int((time.monotonic() - started) * 1000),
            model=model,
            provider=self.name,
            cost_estimated=cost is not None,
        )
