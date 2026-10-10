"""Code Builder: fills the method bodies the generator left to a model.

The generator writes every file, class and signature; a body it cannot derive throws
``NotImplementedException("agent fills: ...")`` (or calls ``AgentFill.Required``) under the TIBCO
expression kept as a ``// TIBCO`` comment, and the file is marked ``origin="agent"``. The model
returns the whole file with those bodies written; plain code then checks the result
(:func:`check_filled_file`) and rejects it with a reason, retrying once.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from engine.analysis.models import Rule
from engine.dotnet_gen.generator import GeneratedSolution, GenFile, ident
from engine.dotnet_gen.models import DesignRecord
from engine.ir.models import Process

from .designer import rules_block
from .jsonout import JsonOutputError
from .prompting import ask_json, memory_block, render_process, render_prompt, wrap_source
from .provider import MemoryItem, mask_sensitive

log = logging.getLogger(__name__)

BUILD_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        }
    },
    "required": ["files"],
}

CONTRACT_TEXT_LIMIT = 6000

# ------------------------------------------------------------ C# text checks

PLACEHOLDER = re.compile(r'NotImplementedException\(\s*"agent fills:|AgentFill\.Required\(')
_ACCESS = re.compile(r"^(?:public|private|protected|internal)\b")
_TIBCO_COMMENT = re.compile(r"^//\s*TIBCO\b")
_ASSERT = re.compile(r"\bAssert\b")
_TEST_ATTR = re.compile(r"\[\s*(?:Fact|Theory)\b")
_PRAGMA = re.compile(r"#\s*pragma\s+warning\s+disable")
# Directives that load a file, string literals holding an absolute filesystem path, a home path,
# a file URI or a parent-directory step. The roots are ones no HTTP route of a migrated service
# uses ("/health", "/api/...", "/dev/token" are routes, not files), so route literals pass.
_OUTSIDE = re.compile(
    r"""^\s*\#\s*(?:r|load|include)\b
      | "(?:[A-Za-z]:[\\/]|\\\\|~/|file:/
           |/(?:etc|home|usr|var|tmp|root|opt|mnt|proc|Users|Volumes)/)
      | (?:^|["\s/\\])\.\.[\\/]""",
    re.VERBOSE | re.MULTILINE,
)


def _norm(line: str) -> str:
    return " ".join(line.split())


def _string_end(text: str, i: int) -> int:
    """Index just past the C# string or char literal starting at ``i`` (regular, interpolated,
    verbatim, raw)."""
    n = len(text)
    j = i
    while j < n and text[j] in "$@":
        j += 1
    verbatim = "@" in text[i:j]
    quote = text[j]
    if quote == '"' and text.startswith('"""', j):
        k = j
        while k < n and text[k] == '"':
            k += 1
        fence = text[j:k]
        end = text.find(fence, k)
        return n if end < 0 else end + len(fence)
    k = j + 1
    while k < n:
        c = text[k]
        if c == "\\" and not verbatim:
            k += 2
            continue
        if c == quote:
            if verbatim and k + 1 < n and text[k + 1] == quote:
                k += 2
                continue
            return k + 1
        if c == "\n" and not verbatim:
            return k
        k += 1
    return n


def strip_comments(text: str) -> str:
    """``text`` with ``//`` and ``/* */`` comments blanked out (newlines kept, so line numbers
    hold) and string and char literals left alone."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
            continue
        if c in "\"'" or (c in "$@" and re.match(r'[$@]{1,3}"', text[i:i + 4])):
            j = _string_end(text, i)
            out.append(text[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def code_only(text: str) -> str:
    """``text`` with comments removed and the contents of string and char literals blanked (the
    quotes stay), so braces, semicolons and words inside literals do not count as code."""
    stripped = strip_comments(text)
    out: list[str] = []
    i, n = 0, len(stripped)
    while i < n:
        c = stripped[i]
        if c in "\"'" or (c in "$@" and re.match(r'[$@]{1,3}"', stripped[i:i + 4])):
            j = _string_end(stripped, i)
            out.append(c + "".join("\n" if ch == "\n" else " " for ch in stripped[i + 1:j - 1])
                       + (stripped[j - 1] if j - 1 > i else ""))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _strip_block_comments(text: str) -> str:
    """Block comments blanked out; line comments kept (a ``// TIBCO`` line inside ``/* */`` does
    not count as kept)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(text[i:j])
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif text[i] in "\"'" or (text[i] in "$@" and re.match(r'[$@]{1,3}"', text[i:i + 4])):
            j = _string_end(text, i)
            out.append(text[i:j])
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def declarations(text: str) -> list[str]:
    """Member and type declarations that end in a parameter list (methods, constructors, primary
    constructors), normalised, from the code with comments removed. Expression-bodied members and
    statements are not declarations."""
    out: list[str] = []
    for line in strip_comments(text).splitlines():
        s = _norm(line)
        if _ACCESS.match(s) and "(" in s and s.endswith(")") and "=>" not in s and ";" not in s:
            out.append(s)
    return out


def tibco_comments(text: str) -> list[str]:
    """The ``// TIBCO`` line comments, normalised (not those inside a block comment)."""
    return [
        _norm(ln) for ln in _strip_block_comments(text).splitlines()
        if _TIBCO_COMMENT.match(ln.strip())
    ]


def is_test_path(path: str) -> bool:
    """True for anything under a tests folder or a ``*.Tests`` project folder."""
    parts = PurePosixPath(path.replace("\\", "/")).parts[:-1]
    return any(p.lower() == "tests" or p.lower().endswith(".tests") for p in parts)


def _introduced(pattern: re.Pattern[str], original: str, new: str) -> bool:
    return len(pattern.findall(new)) > len(pattern.findall(original))


def check_filled_file(
    path: str, original: str, new: str, require_filled: bool = True
) -> str | None:
    """Why ``new`` may not replace ``original`` at ``path``, or ``None`` when it may.

    The file must keep every declaration with a parameter list and every ``// TIBCO`` comment of
    the original, must not introduce ``Assert``, ``[Fact]``/``[Theory]``, ``#pragma warning
    disable`` or a reference to a file outside the solution, and must not be a test file. With
    ``require_filled`` no ``agent fills`` placeholder may remain."""
    if is_test_path(path):
        return f"{path} is a test file; the builder never edits tests"
    wanted = declarations(original)
    if PLACEHOLDER.search(strip_comments(original)) and not wanted:
        return "no method signature found around the placeholder; the file cannot be checked"
    present = set(declarations(new))
    missing = [d for d in wanted if d not in present]
    if missing:
        return f"the method signature changed or is missing: {missing[0]}"
    kept = set(tibco_comments(new))
    lost = [c for c in tibco_comments(original) if c not in kept]
    if lost:
        return f"the TIBCO expression comment is missing: {lost[0]}"
    if _introduced(_ASSERT, original, new):
        return "the code contains Assert; assertions belong to tests"
    if _introduced(_TEST_ATTR, original, new):
        return "the code contains a [Fact] or [Theory] test attribute"
    if _introduced(_PRAGMA, original, new):
        return "the code introduces #pragma warning disable"
    if _introduced(_OUTSIDE, original, new):
        return "the code references a file outside the solution"
    if require_filled and PLACEHOLDER.search(new):
        return "an agent fills placeholder is still in the file"
    return None


# ------------------------------------------------------------ prompt context


@dataclass
class BuildRejection:
    path: str
    attempt: int
    reason: str


@dataclass
class _Target:
    file: GenFile
    activities: list[str]  # activity ids the file's placeholders and mapping rows refer to
    placeholders: list[tuple[str, str]]  # (enclosing signature, placeholder line)


def _placeholders(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    signature = ""
    for line in text.splitlines():
        s = _norm(line)
        if s in declarations(line):
            signature = s
        if PLACEHOLDER.search(line):
            out.append((signature, s))
    return out


_FILL_NAME = re.compile(r'(?:agent fills: |AgentFill\.Required\(")([^"]+)"')


def _process_paths(design: DesignRecord) -> set[str]:
    """Files the process's own mapping rows point at. Call rows point at the called process's
    service, which that process fills, and the solution layout lists those files too, so neither
    is used."""
    return {f"src/{r.project}/{r.file}" for r in design.mapping if r.pattern != "call.process"}


PER_PROCESS_SUFFIXES = ("State", "Service", "Mappings", "Conditions", "Data", "Repository")


def _claims(f: GenFile, design: DesignRecord, process: Process) -> bool:
    """Whether ``process`` owns ``f``. ``GenFile.process_id`` decides when set; a shared file
    (``process_id`` None) is owned through an exact path on one of the process's mapping rows or
    an exact ``<Process><Suffix>.cs`` name."""
    if f.process_id is not None:
        return f.process_id == process.id
    if f.path in _process_paths(design):
        return True
    stem = re.escape(ident(process.name, "Process"))
    suffixes = "|".join(PER_PROCESS_SUFFIXES)
    return re.fullmatch(rf"{stem}(?:{suffixes})\.cs", PurePosixPath(f.path).name) is not None


def _fillable(f: GenFile) -> bool:
    return f.origin == "agent" and not is_test_path(f.path) and bool(PLACEHOLDER.search(f.content))


def claimed_files(
    solution: GeneratedSolution,
    design: DesignRecord,
    process: Process,
    others: list[tuple[DesignRecord, Process]] | None = None,
) -> tuple[list[GenFile], list[BuildRejection]]:
    """The agent-written, non-test files with a placeholder that ``process`` owns, and a
    rejection for each such file another process in ``others`` also claims (neither fills it)."""
    owned: list[GenFile] = []
    rejected: list[BuildRejection] = []
    for f in solution.files:
        if not _fillable(f) or not _claims(f, design, process):
            continue
        rivals = sorted(p.id for d, p in others or [] if p.id != process.id and _claims(f, d, p))
        if rivals:
            rejected.append(BuildRejection(
                path=f.path, attempt=0,
                reason=f"claimed by more than one process: {', '.join([process.id, *rivals])}",
            ))
            continue
        owned.append(f)
    return owned, rejected


def _targets(files: list[GenFile], design: DesignRecord, process: Process) -> list[_Target]:
    """Prompt context for each owned file: the activities its rows and placeholders name."""
    by_name = {a.name: a.id for a in process.activities}
    ids = {a.id for a in process.activities}
    out: list[_Target] = []
    for f in files:
        acts: list[str] = []
        if f.activity_id in ids:
            acts.append(f.activity_id)
        acts += [r.activity_id for r in design.mapping
                 if f"src/{r.project}/{r.file}" == f.path and r.pattern != "call.process"]
        for label in _FILL_NAME.findall(f.content):
            head = label.split(" (", 1)[0].split(":", 1)[0].strip()
            if head.startswith("transition "):
                tid = head.split(" ", 1)[1]
                acts += [t.from_id for t in process.transitions if t.id == tid]
                acts += [t.to_id for t in process.transitions if t.id == tid]
            elif head in by_name:
                acts.append(by_name[head])
        acts = [a for a in dict.fromkeys(acts) if a in ids]
        out.append(_Target(file=f, activities=acts, placeholders=_placeholders(f.content)))
    return out


def focused_process(process: Process, activity_ids: list[str]) -> Process:
    """The process cut down to the given activities and the transitions that touch them."""
    if not activity_ids:
        return process
    keep = set(activity_ids)
    return process.model_copy(update={
        "activities": [a for a in process.activities if a.id in keep],
        "transitions": [t for t in process.transitions if t.from_id in keep or t.to_id in keep],
        "groups": [g for g in process.groups if keep & set(g.member_ids)],
        "fault_handlers": [h for h in process.fault_handlers if keep & set(h.activity_ids)],
        "calls": [c for c in process.calls if c.activity_id in keep],
    })


def rules_for(rules: list[Rule], design: DesignRecord, activity_ids: list[str]) -> list[Rule]:
    """Rules carried by these activities: named on their mapping rows or citing them."""
    acts = set(activity_ids)
    named = {rid for r in design.mapping if r.activity_id in acts for rid in r.rule_ids}
    return [
        r for r in rules
        if r.id in named or any(c.activity_id in acts for c in r.citations)
    ]


def _expressions(process: Process, activity_ids: list[str]) -> list[str]:
    acts = set(activity_ids)
    lines = [
        f"{a.name} ({a.id}): {b.target} = {b.expression}"
        for a in process.activities if a.id in acts for b in a.input_bindings
    ]
    lines += [
        f"transition {t.id} ({t.from_id} to {t.to_id}): {t.expression}"
        for t in process.transitions
        if t.expression and (t.from_id in acts or t.to_id in acts)
    ]
    return lines


def contract_text(solution: GeneratedSolution, design: DesignRecord) -> str:
    """The contract records of the design and the C# files that define them."""
    files = {f.path: f.content for f in solution.files}
    core = next((p.name for p in design.solution.projects if p.kind == "core"), "")
    lines: list[str] = []
    for c in design.contracts:
        lines.append(
            f"- {c.contract_id}: records {', '.join(c.record_names) or '(none)'} "
            f"in {c.namespace or '(no namespace)'}"
        )
        text = files.get(f"src/{core}/{c.file}") if core and c.file else None
        if text:
            lines.append(text[:CONTRACT_TEXT_LIMIT])
    return "\n".join(lines) or "(none)"


def _targets_block(t: _Target, process: Process) -> str:
    lines = [f"File: {t.file.path}", "Methods to fill (signature, then the placeholder):"]
    lines += [f"- {sig or '(file level)'}\n    {ph}" for sig, ph in t.placeholders]
    exprs = _expressions(process, t.activities)
    lines.append("TIBCO expressions for these steps:")
    lines += [f"- {e}" for e in exprs] or ["- (none beyond the comments in the file)"]
    lines += ["Current content:", t.file.content]
    return "\n".join(lines)


# ------------------------------------------------------------ agent


def _parse_files(data: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in data.get("files") or []:
        if isinstance(item, dict) and isinstance(item.get("path"), str) \
                and isinstance(item.get("content"), str):
            out[item["path"].strip().removeprefix("./")] = item["content"]
    return out


async def _fill_one(
    t: _Target,
    solution: GeneratedSolution,
    design: DesignRecord,
    process: Process,
    rules: list[Rule],
    memory_items: list[MemoryItem],
    provider: Any,
    rejections: list[BuildRejection] | None,
) -> GenFile | None:
    system, user = render_prompt(
        "builder",
        process_name=process.name,
        source=render_process(focused_process(process, t.activities), []),
        targets=_targets_block(t, process),
        design=contract_text(solution, design),
        rules=rules_block(rules_for(rules, design, t.activities)),
        memory=memory_block(memory_items),
    )
    reason = ""
    for attempt in (1, 2):
        # The reason can quote TIBCO text (a lost // TIBCO comment), so it goes in as data.
        prompt = user if not reason else (
            f"{user}\n\nYour previous reply was rejected by the checks. The reason, as data:\n"
            f"{wrap_source(reason)}\nReturn the target file again with that fixed."
        )
        try:
            data, _ = await ask_json(
                provider, "build", process, system, prompt, BUILD_SCHEMA,
                tags={"stage": "build", "process": process.id, "file": t.file.path},
            )
        except JsonOutputError as exc:
            reason = f"the reply was not valid JSON ({exc})"
        else:
            files = _parse_files(data)
            new = files.get(t.file.path)
            if new is None:
                reason = f"the reply did not contain {t.file.path}"
            else:
                reason = check_filled_file(t.file.path, t.file.content, new) or ""
                if not reason:
                    if not new.endswith("\n"):
                        new += "\n"
                    return t.file.model_copy(update={"content": new})
        log.warning("builder rejected %s (attempt %d): %s", t.file.path, attempt,
                    mask_sensitive(reason))
        if rejections is not None:
            rejections.append(BuildRejection(path=t.file.path, attempt=attempt, reason=reason))
    return None


async def fill_method_bodies(
    solution: GeneratedSolution,
    design: DesignRecord,
    process: Process,
    rules: list[Rule],
    memory_items: list[MemoryItem],
    provider: Any,
    rejections: list[BuildRejection] | None = None,
    others: list[tuple[DesignRecord, Process]] | None = None,
) -> list[GenFile]:
    """Fill the placeholder bodies of this process's agent-written files, one model call per file.

    Returns only ``origin="agent"`` files whose new content passed :func:`check_filled_file`;
    code-generated files, test files and other processes' files are never sent or returned
    (ownership: :func:`claimed_files`; pass the solution's other processes as ``others`` so a file
    two processes claim is rejected instead of filled). A rejected reply is retried once with the
    reason; a file rejected twice is left out. Every rejection is appended to ``rejections``
    (``attempt`` 0 for an ownership conflict). ``solution`` is not modified."""
    files, conflicts = claimed_files(solution, design, process, others)
    for c in conflicts:
        log.warning("builder skipped %s: %s", c.path, c.reason)
    if rejections is not None:
        rejections.extend(conflicts)
    out: list[GenFile] = []
    for t in _targets(files, design, process):
        filled = await _fill_one(t, solution, design, process, rules, memory_items, provider,
                                 rejections)
        if filled is not None:
            out.append(filled)
    return out
