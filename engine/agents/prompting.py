"""Prompt templates and the text renderers that feed them.

Templates live in ``prompts/*.md`` and have two parts split by a ``<!-- user -->`` line: the
system prompt and the user message. The prompts that name the target language
(``STACK_PROMPTS``) may have a per-stack copy in ``prompts/<pack>/``; the top level is the .NET
pack. ``{placeholders}`` are filled with ``str.format_map`` over a dict that leaves unknown
placeholders untouched. TIBCO content is wrapped in ``<source>`` and is
never masked: citations have to quote it verbatim.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from engine.ir.models import Contract, Process

from .expert_rules import prompt_section
from .jsonout import JsonOutputError, missing_required, parse_json_block
from .provider import LLMRequest, LLMResponse, Tier

PROMPT_DIR = Path(__file__).parent / "prompts"
USER_MARKER = "<!-- user -->"
CONTRACT_TEXT_LIMIT = 6000


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


#: Placeholders a pack may carry before every caller fills them (X9): rendered empty when absent,
#: so the literal ``{iteration}`` never reaches a model.
OPTIONAL_PLACEHOLDERS = {"iteration": ""}


def render(template: str, **values: Any) -> str:
    return template.format_map(_SafeDict({**OPTIONAL_PLACEHOLDERS, **values}))


def split_prompt(text: str) -> tuple[str, str]:
    system, sep, user = text.partition(USER_MARKER)
    if not sep:
        return "", text.strip()
    return system.strip(), user.strip()


# The prompts whose rules name the target language (spec section 3). Each stack's pack folder may
# hold its own copy; the shared ones (analyst, challenger, domain_namer, risk) run before a target
# matters and are never looked up in a pack.
STACK_PROMPTS = ("builder", "repair", "test_engineer", "designer", "review_apply",
                 "reviewer_equivalence", "reviewer_security", "reviewer_standards", "reviewer_test")


def prompt_pack(stack: str) -> str:
    """The folder under ``prompts/`` holding ``stack``'s pack; ``""`` is the top level (.NET).

    An implemented stack names its own (``StackGenerator.prompt_pack``); a known stack that is not
    registered yet uses its key, so a pack can be written and checked before its generator lands.
    ``KeyError`` for a key outside ``STACK_KEYS``."""
    from engine.stacks import STACK_KEYS, get_stack

    if stack not in STACK_KEYS:
        raise KeyError(f"unknown stack {stack!r}")
    try:
        return get_stack(stack).prompt_pack
    except KeyError:
        return "" if stack == "dotnet" else stack


def load_prompt(name: str, stack: str = "dotnet") -> tuple[str, str]:
    """``(system, user)`` of prompt ``name`` for ``stack``: ``prompts/<pack>/<name>.md`` when the
    stack has a pack holding it and ``name`` is one of ``STACK_PROMPTS``, else ``prompts/<name>.md``."""
    path = PROMPT_DIR / f"{name}.md"
    pack = prompt_pack(stack) if name in STACK_PROMPTS else ""
    if pack and (PROMPT_DIR / pack / f"{name}.md").is_file():
        path = PROMPT_DIR / pack / f"{name}.md"
    return split_prompt(path.read_text(encoding="utf-8"))


# Values that carry TIBCO-derived or SME-authored text are always wrapped as data. Callers pass
# them raw; wrapping happens here so no template can be fed unwrapped content.
DATA_KEYS = frozenset({
    "source", "process_name", "rules", "findings", "memory", "design", "targets", "code",
    "failures", "files", "references",
})
# Data blocks a template may leave out: wrapped like ``DATA_KEYS`` when given, empty when not (the
# ``{iteration}`` block of an iteration's failures, logs and requirements).
OPTIONAL_DATA_KEYS = frozenset({"iteration"})
_SOURCE_TAG = re.compile(r"<\s*(/?)\s*source\s*>", re.IGNORECASE)


def render_prompt(name: str, stack: str = "dotnet", **values: Any) -> tuple[str, str]:
    system, user = load_prompt(name, stack)
    wrapped = {
        k: (wrap_source(str(v), inline=(k == "process_name")) if k in DATA_KEYS
            else (wrap_source(str(v)) if v else "") if k in OPTIONAL_DATA_KEYS else v)
        for k, v in values.items()
    }
    system = render(system, **wrapped)
    extra = prompt_section(name)  # the project's expert rules for this phase; "" when none
    if extra:
        system = f"{system}\n\n{extra}" if system else extra
    return system, render(user, **wrapped)


def wrap_source(text: str, inline: bool = False) -> str:
    """Wrap content so the model treats it as data. Any ``<source>`` or ``</source>`` tag inside
    the content (any case, any inner whitespace) has its ``<`` escaped to ``&lt;`` so it cannot end
    the block early."""
    safe = _SOURCE_TAG.sub(lambda m: "&lt;" + m.group(0)[1:], text)
    sep = "" if inline else "\n"
    return f"<source>{sep}{safe}{sep}</source>"


def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif value is not None and value != "":
        out.append((prefix, str(value)))


def render_process(process: Process, contracts: list[Contract]) -> str:
    """Plain-text view of the IR. Values are written verbatim (no JSON escaping) so that text
    quoted from it is found by the citation verifier."""
    lines = [
        (
            f"process id={process.id} name={process.name} module={process.module} "
            f"dialect={process.dialect}"
        )
    ]
    if process.starter:
        lines.append(f"starter activity={process.starter.activity_id} kind={process.starter.kind}")
    for a in process.activities:
        group = f" group={a.group_id}" if a.group_id else ""
        lines.append(f"[activity id={a.id} name={a.name} type={a.type}{group}]")
        flat: list[tuple[str, str]] = []
        _flatten("", a.config, flat)
        for key, val in flat:
            lines.append(f"  config {key}: {val}")
        for b in a.input_bindings:
            lines.append(f"  binding {b.target} ({b.language}): {b.expression}")
        if a.output_schema:
            lines.append(f"  output_schema: {a.output_schema}")
    for t in process.transitions:
        expr = f": {t.expression}" if t.expression else ""
        lines.append(f"[transition id={t.id} from={t.from_id} to={t.to_id} kind={t.kind}]{expr}")
    for g in process.groups:
        lines.append(f"[group id={g.id} kind={g.kind} members={','.join(g.member_ids)}]")
    for fh in process.fault_handlers:
        lines.append(
            f"[fault_handler id={fh.id} fault={fh.fault_name or ''} "
            f"activities={','.join(fh.activity_ids)}]"
        )
    for c in process.calls:
        lines.append(f"[call activity={c.activity_id} target={c.target_process}]")
    for c in contracts:
        lines.append(f"[contract id={c.id} kind={c.kind} roots={','.join(c.root_elements)}]")
        if c.text:
            lines.append(c.text[:CONTRACT_TEXT_LIMIT])
    return "\n".join(lines)


RETRY_NOTE = (
    "Your reply was not valid JSON for the requested schema. Reply again with only the JSON "
    "object, no prose and no code fences."
)


async def ask_json(
    provider: Any,
    task_kind: str,
    process: Process | None,
    system: str,
    user: str,
    schema: dict,
    *,
    tags: dict[str, str] | None = None,
    tier: Tier | None = None,
    max_tokens: int = 8192,
    retries: int = 1,
) -> tuple[dict, LLMResponse]:
    """Send one prompt through a ``RoutedProvider`` and parse the JSON object that comes back.
    An unparseable reply is retried ``retries`` times with a short correction."""
    messages: list[dict] = [{"role": "user", "content": user}]
    attempt = 0
    while True:
        req = LLMRequest(
            system=system,
            messages=list(messages),
            model="",
            max_tokens=max_tokens,
            json_schema=schema,
            tags=dict(tags or {}),
        )
        resp = await provider.complete_for(task_kind, process, req, tier=tier)
        note = RETRY_NOTE
        try:
            data = parse_json_block(resp.text)
            missing = missing_required(data, schema)
            if missing:
                note = f"{RETRY_NOTE} Missing required keys: {', '.join(missing)}."
                raise JsonOutputError(f"missing required keys: {', '.join(missing)}")
            return data, resp
        except JsonOutputError:
            if attempt >= retries:
                raise
            attempt += 1
            messages += [
                {"role": "assistant", "content": resp.text},
                {"role": "user", "content": note},
            ]


def memory_block(items: list[Any]) -> str:
    if not items:
        return "(none)"
    return "\n".join(f"- id={m.id} ({m.kind}, source: {m.source}): {m.text}" for m in items)
