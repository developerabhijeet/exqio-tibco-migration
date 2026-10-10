"""Provider abstraction: request and response types, a fake and a null provider,
the per-model price table and sensitive-value masking.

Keys come from the environment only. Nothing here logs request or response text.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from engine.knowledge.memory import MemoryItem  # noqa: F401  (re-exported; defined by Task 10)

Tier = Literal["strong", "standard", "efficient"]
TIERS: tuple[Tier, ...] = ("strong", "standard", "efficient")


class ProviderError(Exception):
    """A model call failed. The routed provider retries these on the fallback."""


class ProviderUnavailable(ProviderError):
    """The provider is not configured (missing key, endpoint or deployment)."""


class RateLimited(ProviderError):
    """The provider refused the call because of a rate limit or overload."""


@dataclass
class LLMRequest:
    system: str
    messages: list[dict]
    model: str
    max_tokens: int
    json_schema: dict | None = None
    cache: bool = True
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    usage_in: int
    usage_out: int
    cached_tokens: int
    cost_usd: float
    latency_ms: int
    model: str
    provider: str
    # True when cost_usd was computed from the price table (PRICES_USD_PER_MTOK) rather than
    # reported by the provider. False when the provider reported it (Claude Code), when it is a
    # fake, or when the model is not priced and cost_usd is 0.0.
    cost_estimated: bool = False


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(self, req: LLMRequest) -> LLMResponse: ...

    def models(self) -> dict[Tier, str]: ...


class NullProvider:
    """Always unavailable. Lets the routed provider fall straight to its fallback."""

    name = "none"

    async def complete(self, req: LLMRequest) -> LLMResponse:
        raise ProviderUnavailable("no model provider is configured")

    def models(self) -> dict[Tier, str]:
        return {"strong": "", "standard": "", "efficient": ""}


class FakeProvider:
    """Scripted provider for tests. Each response is consumed in order.

    An item that is an ``Exception`` is raised instead of returned. Every request is kept
    in ``requests`` so tests can inspect the exact prompt that was sent.
    """

    def __init__(self, responses: list[str | Exception], name: str = "fake") -> None:
        self.name = name
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        if not self._responses:
            raise ProviderError("FakeProvider has no scripted response left")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        prompt = req.system + "".join(str(m.get("content", "")) for m in req.messages)
        return LLMResponse(
            text=item,
            usage_in=max(1, len(prompt) // 4),
            usage_out=max(1, len(item) // 4),
            cached_tokens=0,
            cost_usd=0.0,
            latency_ms=1,
            model=req.model or "fake-model",
            provider=self.name,
            cost_estimated=False,
        )

    def models(self) -> dict[Tier, str]:
        return {"strong": "fake-strong", "standard": "fake-standard", "efficient": "fake-efficient"}


# ---------------------------------------------------------------- pricing

# (input USD per million tokens, output USD per million tokens). Indicative list prices for the
# Azure OpenAI models likely to back the three deployments; review before quoting cost to a
# customer. Keys are matched as the longest prefix of the model name the API reports.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
}
CACHED_INPUT_DISCOUNT = 0.5  # cached prompt tokens are billed at half the input price


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cached: int = 0) -> float | None:
    """Cost in USD from the price table, or None when the model is not priced."""
    name = (model or "").lower()
    matches = [k for k in PRICES_USD_PER_MTOK if name.startswith(k)]
    if not matches:
        return None
    price_in, price_out = PRICES_USD_PER_MTOK[max(matches, key=len)]
    cached = max(0, min(cached, tokens_in))
    fresh = tokens_in - cached
    total = fresh * price_in + cached * price_in * CACHED_INPUT_DISCOUNT + tokens_out * price_out
    return total / 1_000_000


# ---------------------------------------------------------------- masking

SENSITIVE_FIELDS = ("ssn", "account_number", "card_number")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
MASK = "***"

# A key is sensitive when it contains the field name as whole words, so borrower_ssn,
# ssn_last4, customerSsn and ns:accountNumber match but classname does not. The field name may be
# joined by "_", "-", "." or nothing, or by camel case (account_number, accountNumber).
_PREFIX = r"(?:[A-Za-z0-9]*[_\-.])*(?:[a-z0-9]+(?=[A-Z]))?"
_SUFFIX = r"(?:[_\-.][A-Za-z0-9_\-.]*|[A-Z][A-Za-z0-9]*)?"
_VALUE = r"""(?P<val>"[^"]*"|'[^']*'|[^\s,;}\]&<"']+)"""


def _field_alt(fields: Iterable[str]) -> str:
    alts: list[str] = []
    for f in fields:
        parts = [p for p in re.split(r"[_\-.\s]+|(?<=[a-z])(?=[A-Z])", f.strip()) if p]
        if parts:
            alts.append("(?i:" + r"[_\-.]?".join(re.escape(p) for p in parts) + ")")
    return "|".join(sorted(set(alts), key=len, reverse=True))


def _field_pattern(fields: Iterable[str]) -> re.Pattern[str]:
    alt = _field_alt(fields)
    key = rf"{_PREFIX}(?:{alt}){_SUFFIX}"
    return re.compile(
        rf"""
        (?P<pair>
          (?P<q>["']?)(?<![A-Za-z0-9])(?P<key>{key})(?P=q)
          (?P<sep>\s*[:=]\s*)
          {_VALUE}
        )
        |
        (?P<element>
          (?P<open><(?P<oname>(?:[A-Za-z0-9_.\-]+:)?{key})(?P<attrs>\s[^<>]*)?>)
          (?P<body>[^<]*)
          (?P<close></(?P=oname)\s*>)
        )
        """,
        re.VERBOSE,
    )


_DEFAULT_PATTERN = _field_pattern(SENSITIVE_FIELDS)


def mask_sensitive(text: str, extra_fields: Iterable[str] = ()) -> str:
    """Mask SSNs and the values of sensitive fields before text reaches a log, a trace, a tag
    or an embedding. Never apply it to a prompt: citations must quote the source verbatim.

    Handles ``key: value``, ``key=value``, JSON ``"key": "value"`` and ``<key>value</key>``
    (with namespace prefix and attributes). ``extra_fields`` adds field names tagged sensitive
    in configuration."""
    if not text:
        return text
    extra = [f for f in extra_fields if f]
    pattern = _field_pattern([*SENSITIVE_FIELDS, *extra]) if extra else _DEFAULT_PATTERN

    def repl(m: re.Match[str]) -> str:
        if m.group("element"):
            return f"{m.group('open')}{MASK}{m.group('close')}"
        val = m.group("val")
        masked = f"{val[0]}{MASK}{val[0]}" if val[:1] in "\"'" and len(val) >= 2 else MASK
        return f"{m.group('q')}{m.group('key')}{m.group('q')}{m.group('sep')}{masked}"

    return _SSN.sub("***-**-****", pattern.sub(repl, text))
