"""Repair loop (design spec 2.5): plain code runs the tests and decides; a model only proposes diffs.

On a failing run the builder model gets the failing test names and messages, the rule text and the
TIBCO step behind them, and answers with a unified diff. Code applies the diff only when every file
it touches is an existing agent-written production file inside the solution root (never under a
tests folder, never a code-generated scaffold file, never outside the root) and the patched file
still passes the builder's checks; then it rebuilds and retests. A diff that breaks the build is
rolled back, so every attempt starts from the last compiling state. At most ``max_attempts`` diffs
are asked for and every attempt is recorded.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from pydantic import BaseModel, Field

from engine.agents.builder import check_filled_file, focused_process, is_test_path
from engine.agents.designer import rules_block
from engine.agents.jsonout import JsonOutputError
from engine.agents.prompting import ask_json, render_process, render_prompt
from engine.agents.provider import mask_sensitive
from engine.analysis.models import Rule
from engine.dotnet_gen.generator import GeneratedSolution
from engine.dotnet_gen.runner import BuildResult, TestRun
from engine.ir.models import Process

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
FAILURE_LOG_LIMIT = 4000

REPAIR_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "diff": {"type": "string", "description": "A unified diff against the files given"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["diff", "notes"],
}


class RepairAttempt(BaseModel):
    n: int
    failing: list[str] = Field(default_factory=list)
    diff: str = ""
    # True when the diff is in the solution after this attempt: it passed the gate and the
    # solution still builds. A diff that broke the build was rolled back and is False.
    applied: bool = False
    reason: str = ""  # why the diff was rejected or rolled back; empty when it was applied


class RepairResult(BaseModel):
    """``attempts`` in order; ``final`` is the test run of the solution as left on disk.

    The solution on disk (and ``solution.files``) always ends in a compiling state: diffs that
    broke the build were rolled back, so ``final`` is the last test run of a build that compiled
    (or the first run when nothing was applied)."""

    attempts: list[RepairAttempt] = Field(default_factory=list)
    final: TestRun


class Runner(Protocol):
    def build(self) -> BuildResult: ...

    def test(self) -> TestRun: ...


AttemptRecorder = Callable[[RepairAttempt], Awaitable[None] | None]


# ------------------------------------------------------------ unified diffs


class PatchError(ValueError):
    """A diff that cannot be parsed or does not apply."""


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
_BINARY = re.compile(r"^(?:GIT binary patch|Binary files .* differ|literal \d+|delta \d+)",
                     re.MULTILINE)


@dataclass
class FilePatch:
    old: str | None  # None for /dev/null (a new file)
    new: str | None  # None for /dev/null (a deleted file)
    hunks: list[tuple[int, list[str]]] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new or self.old or ""


def _diff_path(header: str) -> str | None:
    p = header.split("\t", 1)[0].strip()
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def parse_unified_diff(diff: str) -> list[FilePatch]:
    """Parse ``--- / +++ / @@`` sections. Hunk line counts are not trusted (models miscount);
    a hunk runs to the next hunk or file header."""
    if _BINARY.search(diff) or "\0" in diff:
        raise PatchError("the diff has a binary section; only text diffs are applied")
    lines = diff.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    patches: list[FilePatch] = []
    cur: FilePatch | None = None
    body: list[str] | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            cur = FilePatch(old=_diff_path(line[4:]), new=_diff_path(lines[i + 1][4:]))
            patches.append(cur)
            body = None
            i += 2
            continue
        if line.startswith("@@"):
            m = _HUNK.match(line)
            if cur is None or not m:
                raise PatchError(f"malformed hunk header: {line[:80]}")
            body = []
            cur.hunks.append((int(m.group(1)), body))
        elif body is not None:
            if line.startswith("\\"):
                pass  # "\ No newline at end of file"
            elif line == "" or line[0] in " +-":
                body.append(line if line else " ")
            elif line.startswith("diff "):
                body = None
            else:
                raise PatchError(f"unexpected line in a hunk: {line[:80]}")
        i += 1
    if not patches:
        raise PatchError("the diff names no file")
    return patches


def _matches(lines: list[str], at: int, block: list[str]) -> bool:
    if at < 0 or at + len(block) > len(lines):
        return False
    return all(lines[at + k].rstrip() == b.rstrip() for k, b in enumerate(block))


def apply_patch(text: str, patch: FilePatch) -> str:
    """Apply one file's hunks. Context must match (trailing whitespace ignored); a hunk found at
    another line than its header says is applied at the nearest match."""
    lines = text.split("\n")
    offset = 0
    for k, (start, body) in enumerate(patch.hunks, 1):
        old = [ln[1:] for ln in body if ln[0] in " -"]
        new = [ln[1:] for ln in body if ln[0] in " +"]
        expected = max(start - 1, 0) + offset
        if not old:
            at = min(expected, len(lines))
        else:
            hits = [p for p in range(len(lines) - len(old) + 1) if _matches(lines, p, old)]
            if not hits:
                raise PatchError(f"hunk {k} for {patch.path} does not match the file")
            at = min(hits, key=lambda p: abs(p - expected))
        lines[at:at + len(old)] = new
        offset += len(new) - len(old)
    return "\n".join(lines)


def apply_unified_diff(files: dict[str, str], diff: str) -> dict[str, str]:
    """New content for each file the diff changes. Raises :class:`PatchError` when the diff names
    a file not in ``files``, creates or deletes a file, or a hunk does not match."""
    out: dict[str, str] = {}
    for patch in parse_unified_diff(diff):
        if patch.old is None or patch.new is None:
            raise PatchError(f"{patch.path}: the diff creates or deletes a file")
        if patch.old != patch.new:
            raise PatchError(f"{patch.old}: the diff renames a file")
        if patch.path not in files:
            raise PatchError(f"{patch.path}: no file of that name in the solution")
        out[patch.path] = apply_patch(out.get(patch.path, files[patch.path]), patch)
    return out


# ------------------------------------------------------------ gate


def _path_problem(path: str, root: Path, agent_paths: set[str], known: set[str]) -> str | None:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", path):
        return f"{path} is outside the solution root"
    try:
        (root / p).resolve().relative_to(root.resolve())
    except ValueError:
        return f"{path} is outside the solution root"
    if is_test_path(path):
        return f"{path} is a test file; repairs fix the code, never the tests"
    if path not in known:
        return f"{path} is not a file of the solution"
    if path not in agent_paths:
        return f"{path} is generated by code, not agent-written; fix the generator instead"
    return None


def check_diff(diff: str, solution: GeneratedSolution) -> tuple[dict[str, str], str]:
    """``(patched files, "")`` when the diff may be applied, else ``({}, reason)``."""
    if not diff.strip():
        return {}, "the reply contained no diff"
    try:
        patches = parse_unified_diff(diff)
    except PatchError as exc:
        return {}, str(exc)
    known = {f.path for f in solution.files}
    agent = {f.path for f in solution.files if f.origin == "agent"}
    for patch in patches:
        for path in {patch.old, patch.new} - {None}:
            problem = _path_problem(str(path), solution.root, agent, known)
            if problem:
                return {}, problem
    current = {f.path: f.content for f in solution.files}
    try:
        patched = apply_unified_diff(current, diff)
    except PatchError as exc:
        return {}, str(exc)
    for path, text in patched.items():
        problem = check_filled_file(path, current[path], text, require_filled=False)
        if problem:
            return {}, f"{path}: {problem}"
    return patched, ""


# ------------------------------------------------------------ prompt context


def _failing(run: TestRun) -> list[str]:
    return [c.name for c in run.cases if c.outcome == "failed"]


def _failures_block(run: TestRun, previous: RepairAttempt | None) -> str:
    lines: list[str] = []
    for c in run.cases:
        if c.outcome != "failed":
            continue
        ids = ", ".join(x for x in (c.rule_id, c.branch_id, c.scenario_id) if x)
        lines.append(f"- {c.name}{f' ({ids})' if ids else ''}: {c.message.strip()}")
    if not lines:
        lines.append("The build or the test run failed before any test result:")
        lines.append((run.log or "(no log)")[-FAILURE_LOG_LIMIT:])
    if previous is not None and not previous.applied and previous.reason:
        lines.append(f"Your previous diff was not kept: {previous.reason}")
    return "\n".join(lines)


def _rules_and_steps(run: TestRun, process: Process, rules: list[Rule]) -> tuple[list[Rule], Process]:
    """The rules the failing tests name and the TIBCO steps those rules cite; all of them when the
    failures name no rule."""
    failing_rules = {c.rule_id for c in run.cases if c.outcome == "failed" and c.rule_id}
    picked = [r for r in rules if r.id in failing_rules and r.status != "rejected"]
    acts = [c.activity_id for r in picked for c in r.citations]
    for c in run.cases:
        if c.outcome == "failed" and c.branch_id:
            acts += [t.from_id for t in process.transitions if t.id == c.branch_id]
            acts += [t.to_id for t in process.transitions if t.id == c.branch_id]
    if not picked:
        return rules, process
    return picked, focused_process(process, list(dict.fromkeys(acts)))


def _files_block(solution: GeneratedSolution) -> str:
    out: list[str] = []
    for f in solution.files:
        if f.origin == "agent" and not is_test_path(f.path):
            out += [f"=== {f.path}", f.content]
    return "\n".join(out) or "(none)"


# ------------------------------------------------------------ loop


async def _call(fn: Callable[[], Any]) -> Any:
    return await asyncio.to_thread(fn)


async def _record(recorder: AttemptRecorder | None, attempt: RepairAttempt) -> None:
    if recorder is None:
        return
    result = recorder(attempt)
    if inspect.isawaitable(result):
        await result


def _write(solution: GeneratedSolution, patched: dict[str, str]) -> None:
    """Write patched files to disk and into ``solution.files`` (same order, same origin)."""
    for path, text in patched.items():
        target = solution.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    solution.files = [
        f.model_copy(update={"content": patched[f.path]}) if f.path in patched else f
        for f in solution.files
    ]


async def repair_loop(
    runner: Runner,
    solution: GeneratedSolution,
    process: Process,
    rules: list[Rule],
    provider: Any,
    max_attempts: int = MAX_ATTEMPTS,
    recorder: AttemptRecorder | None = None,
) -> RepairResult:
    """Test, and while tests fail ask for a diff, gate it, apply it, rebuild and retest.

    Stops when the tests pass or after ``max_attempts`` diffs were asked for, whether or not they
    were applied. Each attempt is passed to ``recorder`` (sync or async) as it completes. Applied
    diffs are written to ``solution.root`` and into ``solution.files``."""
    run: TestRun = await _call(runner.test)
    attempts: list[RepairAttempt] = []
    previous: RepairAttempt | None = None
    for n in range(1, max_attempts + 1):
        if run.ok:
            break
        failing = _failing(run)
        picked, steps = _rules_and_steps(run, process, rules)
        system, user = render_prompt(
            "repair",
            attempt=str(n),
            process_name=process.name,
            source=render_process(steps, []),
            failures=_failures_block(run, previous),
            files=_files_block(solution),
            rules=rules_block(picked),
        )
        diff, reason, applied = "", "", False
        try:
            data, _ = await ask_json(
                provider, "repair", process, system, user, REPAIR_SCHEMA,
                tags={"stage": "repair", "process": process.id, "attempt": str(n)},
            )
            diff = str(data.get("diff") or "")
            patched, reason = check_diff(diff, solution)
        except JsonOutputError as exc:
            patched, reason = {}, f"the reply was not valid JSON ({exc})"
        if reason:
            log.warning("repair attempt %d for %s rejected: %s", n, process.id,
                        mask_sensitive(reason))
        else:
            before = {f.path: f.content for f in solution.files if f.path in patched}
            _write(solution, patched)
            build: BuildResult = await _call(runner.build)
            if build.ok:
                applied = True
                run = await _call(runner.test)
            else:
                _write(solution, before)  # back to the last compiling state
                errors = "; ".join(build.errors[:5]) or (build.log or "")[-500:]
                reason = (f"the build failed after the diff ({len(build.errors)} error(s)), "
                          f"so it was rolled back: {errors}")
                log.warning("repair attempt %d for %s rolled back: %s", n, process.id,
                            mask_sensitive(reason))
        attempt = RepairAttempt(n=n, failing=failing, diff=diff, applied=applied, reason=reason)
        attempts.append(attempt)
        await _record(recorder, attempt)
        previous = attempt
    return RepairResult(attempts=attempts, final=run)
