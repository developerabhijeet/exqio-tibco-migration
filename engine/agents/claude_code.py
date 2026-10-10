"""Claude via the Claude Agent SDK, authenticated with a Claude Code OAuth token.

Each call is a single turn with every tool disabled and no settings sources, so the model only
writes text. ``ANTHROPIC_API_KEY`` never reaches the child process: the SDK merges
``options.env`` over the parent environment, so the key is blanked there rather than omitted.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    query,
)

from .jsonout import json_instruction
from .provider import (
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    Tier,
)

TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
DEFAULT_MODELS: dict[Tier, str] = {
    "strong": "claude-opus-5-5",
    "standard": "claude-sonnet-5-5",
    "efficient": "claude-haiku-4-5",
}
_RATE_LIMIT_STATUSES = {429, 529}


class ClaudeCodeProvider:
    name = "claude-code"

    def __init__(
        self,
        token: str | None = None,
        env: Mapping[str, str] | None = None,
        query_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._env: Mapping[str, str] = os.environ if env is None else env
        self.token = token or self._env.get(TOKEN_ENV) or None
        self._query = query_fn or query

    @property
    def available(self) -> bool:
        return bool(self.token)

    def models(self) -> dict[Tier, str]:
        return {
            "strong": self._env.get("MODEL_STRONG") or DEFAULT_MODELS["strong"],
            "standard": self._env.get("MODEL_STANDARD") or DEFAULT_MODELS["standard"],
            "efficient": self._env.get("MODEL_EFFICIENT") or DEFAULT_MODELS["efficient"],
        }

    def child_env(self) -> dict[str, str]:
        """The environment the model process is meant to see: the parent's, minus API keys,
        plus the OAuth token."""
        env = {k: v for k, v in self._env.items() if k not in STRIPPED_ENV}
        env[TOKEN_ENV] = self.token or ""
        return env

    def sdk_env(self) -> dict[str, str]:
        """``ClaudeAgentOptions.env``: the SDK layers it over ``os.environ``, so each stripped
        variable present in the parent is overridden with an empty string."""
        out = {k: "" for k in STRIPPED_ENV if k in os.environ or k in self._env}
        out[TOKEN_ENV] = self.token or ""
        return out

    def build_prompt(self, req: LLMRequest) -> str:
        msgs = req.messages
        if len(msgs) == 1 and msgs[0].get("role", "user") == "user":
            return str(msgs[0].get("content", ""))
        return "\n\n".join(f"[{m.get('role', 'user')}]\n{m.get('content', '')}" for m in msgs)

    def build_options(self, req: LLMRequest) -> ClaudeAgentOptions:
        system = req.system + (json_instruction(req.json_schema) if req.json_schema else "")
        return ClaudeAgentOptions(
            model=req.model or None,
            system_prompt=system,
            max_turns=1,
            tools=[],
            allowed_tools=[],
            setting_sources=[],
            env=self.sdk_env(),
        )

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if not self.available:
            raise ProviderUnavailable(f"{TOKEN_ENV} is not set")
        started = time.monotonic()
        texts: list[str] = []
        result: ResultMessage | None = None
        model = req.model
        try:
            async for msg in self._query(prompt=self.build_prompt(req), options=self.build_options(req)):
                if isinstance(msg, AssistantMessage):
                    model = msg.model or model
                    texts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    result = msg
        except ClaudeSDKError as exc:
            raise ProviderError(f"claude-code: {exc}") from exc
        if result is None:
            raise ProviderError("claude-code: the run ended without a result message")
        if result.is_error:
            detail = result.result or "; ".join(result.errors or []) or result.subtype
            err = RateLimited if result.api_error_status in _RATE_LIMIT_STATUSES else ProviderError
            raise err(f"claude-code: {detail}")
        usage = result.usage or {}
        cached = int(usage.get("cache_read_input_tokens") or 0)
        created = int(usage.get("cache_creation_input_tokens") or 0)
        cost = result.total_cost_usd
        return LLMResponse(
            text=result.result if result.result is not None else "".join(texts),
            usage_in=int(usage.get("input_tokens") or 0) + cached + created,
            usage_out=int(usage.get("output_tokens") or 0),
            cached_tokens=cached,
            cost_usd=float(cost or 0.0),
            latency_ms=int((time.monotonic() - started) * 1000),
            model=model,
            provider=self.name,
            cost_estimated=False,  # reported by the SDK, not computed from a price table
        )
