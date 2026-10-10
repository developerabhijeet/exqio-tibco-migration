"""Prompt templates and the text renderers that feed them.

Templates live in ``prompts/*.md`` and have two parts split by a ``<!-- user -->`` line: the
system prompt and the user message. ``{placeholders}`` are filled with ``str.format_map`` over a
dict that leaves unknown placeholders untouched. TIBCO content is wrapped in ``<source>`` and is
never masked: citations have to quote it verbatim.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.ir.models import Contract, Process

from .jsonout import JsonOutputError, parse_json_block
from .provider import LLMRequest, LLMResponse, Tier

PROMPT_DIR = Path(__file__).parent / "prompts"
USER_MARKER = "<!-- user -->"
CONTRACT_TEXT_LIMIT = 6000


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(template: str, **values: Any) -> str:
    return template.format_map(_SafeDict(values))


def split_prompt(text: str) -> tuple[str, str]:
    system, sep, user = text.partition(USER_MARKER)
    if not sep:
        return "", text.strip()
    return system.strip(), user.strip()


def load_prompt(name: str) -> tuple[str, str]:
    return split_prompt((PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8"))


def render_prompt(name: str, **values: Any) -> tuple[str, str]:
    system, user = load_prompt(name)
    return render(system, **values), render(user, **values)


def wrap_source(text: str) -> str:
    """Wrap TIBCO content so the model treats it as data. A literal closing tag inside the
    content is broken so it cannot end the block early."""
    safe = text.replace("</source>", "</ source>")
    return f"<source>\n{safe}\n</source>"


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
        try:
            return parse_json_block(resp.text), resp
        except JsonOutputError:
            if attempt >= retries:
                raise
            attempt += 1
            messages += [
                {"role": "assistant", "content": resp.text},
                {"role": "user", "content": RETRY_NOTE},
            ]


def memory_block(items: list[Any]) -> str:
    if not items:
        return "(none)"
    return "\n".join(f"- id={m.id} ({m.kind}, source: {m.source}): {m.text}" for m in items)
