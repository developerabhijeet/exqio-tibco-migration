"""Test Engineer: writes the bodies of the generated rule and branch tests.

``tests_gen`` writes ``<Process>RulesTests.cs`` (one test per approved rule) and
``<Process>BranchTests.cs`` (one test per branch) with ``Assert.Fail("no test body for ...")`` where
no body could be generated. The model writes those bodies; plain code checks each one
(:func:`check_test_body`), splices it into its method and nothing else, retries a rejected body
once, and leaves a body still rejected as ``Assert.Fail("no test body for <id>: <reason>")`` so gate
G4 stays blocked for an honest reason.
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from engine.analysis.models import Rule
from engine.dotnet_gen import tests_gen
from engine.dotnet_gen.generator import GeneratedSolution, GenFile, cs_string, ident, one_line
from engine.dotnet_gen.models import DesignRecord
from engine.ir.models import Process
from engine.parity.models import Scenario

from .builder import (
    _OUTSIDE,
    _PRAGMA,
    BuildRejection,
    code_only,
    focused_process,
    is_test_path,
    strip_comments,
)
from .designer import rules_block
from .jsonout import JsonOutputError
from .prompting import ask_json, memory_block, render_process, render_prompt, wrap_source
from .provider import MemoryItem, mask_sensitive

log = logging.getLogger(__name__)

TEST_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "The test method name as given"},
                    "body": {"type": "string", "description": "The statements of the body only"},
                },
                "required": ["method", "body"],
            },
        }
    },
    "required": ["tests"],
}

TEST_FILE_KINDS = ("RulesTests", "BranchTests")
DRIVEN_SUFFIXES = ("Service", "Controller", "Consumer")
FIXTURE_TEXT_LIMIT = 3000
REASON_LIMIT = 160

_PLACEHOLDER = re.compile(r'Assert\.Fail\(\s*"no test body for ([^"]*)"\s*\)')
_SIGNATURE = re.compile(r"^    public (?:async )?Task (\w+)\(\)\s*$")
_TRAIT = re.compile(r'\[Trait\("(Rule|Branch)",\s*"([^"]*)"\)\]')
_TRIVIAL = re.compile(r"Assert\.(?:True\(true\)|False\(false\))")
_ASSERT_CALL = re.compile(r"\bAssert\.\w+\s*[<(]")
_TEST_ATTR = re.compile(r"\[\s*(?:Fact|Theory)\b")
_TYPE_DECL = re.compile(
    r"^\s*(?:public|internal)\s+(?:(?:sealed|static|abstract|partial|readonly)\s+)*"
    r"(?:class|record|struct|interface|enum)\s+(\w+)", re.MULTILINE)
_NAMESPACE = re.compile(r"^\s*namespace\s+([\w.]+)\s*;", re.MULTILINE)


# ------------------------------------------------------------ test file parsing


@dataclass
class TestMethod:
    __test__ = False  # not a pytest class

    name: str
    kind: str  # rule | branch
    ref: str  # rule id or transition id
    label: str  # what the placeholder names: "<rule id>" or "branch <transition id>"
    start: int  # index of the signature line
    end: int  # index of the closing brace line


def placeholder_methods(text: str) -> list[TestMethod]:
    """Test methods whose body is still the ``no test body`` placeholder."""
    lines = text.split("\n")
    out: list[TestMethod] = []
    for i, line in enumerate(lines):
        m = _SIGNATURE.match(line)
        if not m or i + 1 >= len(lines) or lines[i + 1] != "    {":
            continue
        end = next((k for k in range(i + 2, len(lines)) if lines[k] == "    }"), None)
        if end is None:
            continue
        body = "\n".join(lines[i + 2:end])
        ph = _PLACEHOLDER.search(body)
        if not ph:
            continue
        kind, ref = "", ""
        for back in lines[max(0, i - 4):i]:
            t = _TRAIT.search(back)
            if t:
                kind, ref = t.group(1).lower(), t.group(2)
        label = ph.group(1).split(": ", 1)[0]
        out.append(TestMethod(name=m.group(1), kind=kind or "rule", ref=ref or label,
                              label=label, start=i, end=end))
    return out


def _test_files(solution: GeneratedSolution, process: Process) -> list[GenFile]:
    stem = ident(process.name, "Process")
    names = {f"{stem}{k}.cs" for k in TEST_FILE_KINDS}
    out: list[GenFile] = []
    for f in solution.files:
        if not is_test_path(f.path) or PurePosixPath(f.path).name not in names:
            continue
        if f.process_id not in (None, process.id):
            continue
        if placeholder_methods(f.content):
            out.append(f)
    return out


# ------------------------------------------------------------ validation


def _statements(body: str) -> list[str]:
    """Statements of ``body`` (comments removed; split on ``;`` outside literals)."""
    stripped = strip_comments(body)
    code = code_only(body)
    out: list[str] = []
    start = 0
    for k, ch in enumerate(code):
        if ch == ";":
            out.append(stripped[start:k + 1])
            start = k + 1
    if stripped[start:].strip():
        out.append(stripped[start:])
    return out


def _driven_types(stem: str) -> list[str]:
    return [f"{stem}{s}" for s in DRIVEN_SUFFIXES]


def check_test_body(body: str, stem: str) -> str | None:
    """Why ``body`` may not become a test body, or ``None`` when it may.

    The body must call a method of ``<stem>Service``, ``<stem>Controller`` or ``<stem>Consumer``
    (through a variable bound to that type or statically), hold an ``Assert.`` whose arguments
    use the result of that call (or the call itself, as in ``Assert.ThrowsAsync``), and contain
    no ``Assert.Fail(``, ``Assert.True(true)``, ``[Fact]``/``[Theory]``, ``#pragma warning
    disable``, file reference outside the solution or unbalanced braces."""
    code = code_only(body)
    if not code.strip():
        return "the body is empty"
    for open_, close in ("{}", "()"):
        depth = 0
        for ch in code:
            depth += (ch == open_) - (ch == close)
            if depth < 0:
                break
        if depth != 0:
            return "the body has unbalanced braces or parentheses"
    if re.search(r"\bAssert\.Fail\s*\(", code):
        return "the body still calls Assert.Fail"
    if _TRIVIAL.search(re.sub(r"\s+", "", code)):
        return "the body asserts a constant; it must assert the behaviour"
    if _TEST_ATTR.search(code):
        return "the body adds a [Fact] or [Theory] attribute"
    if _PRAGMA.search(code):
        return "the body introduces #pragma warning disable"
    if _OUTSIDE.search(strip_comments(body)):
        return "the body references a file outside the solution"
    types = _driven_types(stem)
    type_alt = "|".join(re.escape(t) for t in types)
    statements = _statements(body)
    # a variable bound to an instance: "new T(...)", "GetRequiredService<T>()" or "T name = ..."
    binding = re.compile(rf"\bnew\s+(?:{type_alt})\s*\(|<\s*(?:{type_alt})\s*>")
    bound: set[str] = set()
    for st in statements:
        m = re.search(rf"\b(?:{type_alt})\s+(\w+)\s*=(?!=)", st)
        if m is None and binding.search(st):
            m = re.search(r"\b(\w+)\s*=(?!=)", st)
        if m:
            bound.add(m.group(1))
    receivers = "|".join([*types, *(re.escape(b) for b in bound)])
    call = re.compile(rf"(?:\b(?:{receivers})|\bnew\s+(?:{type_alt})\s*\([^;]*?\))\s*\.\s*\w+\s*[<(]")
    calling = [st for st in statements if call.search(st)]
    if not calling:
        return f"the body does not call the generated {', '.join(types)}"
    results: set[str] = set()
    for st in calling:
        m = re.search(r"\b(\w+)\s*=(?!=)", st.split("=>", 1)[0])
        if m and m.group(1) not in bound:
            results.add(m.group(1))
    for _ in range(3):  # values derived from a result count as results
        for st in statements:
            m = re.search(r"\b(\w+)\s*=(?!=)", st)
            if m and m.group(1) not in results and m.group(1) not in bound and results \
                    and re.search(rf"\b(?:{'|'.join(map(re.escape, results))})\b", st[m.end():]):
                results.add(m.group(1))
    for st in statements:
        if not _ASSERT_CALL.search(st):
            continue
        if call.search(st):
            return None
        if results and re.search(rf"\b(?:{'|'.join(map(re.escape, results))})\b", st):
            return None
    return "no Assert checks a value returned by the generated call"


# ------------------------------------------------------------ splicing


def _indent(body: str) -> list[str]:
    text = textwrap.dedent(body.replace("\r\n", "\n").expandtabs(4)).strip("\n")
    return [("        " + ln) if ln.strip() else "" for ln in text.split("\n")]


def _splice(lines: list[str], m: TestMethod, body: str) -> None:
    """Replace the body of ``m`` in place; the signature gains ``async`` when the body awaits."""
    new = _indent(body)
    code = code_only("\n".join(new))
    awaits = re.search(r"\bawait\b", code) is not None
    sig = lines[m.start].replace("public async Task ", "public Task ")
    if awaits:
        sig = sig.replace("public Task ", "public async Task ")
        new = [ln for ln in new if ln.strip() != "return Task.CompletedTask;"]
    elif not re.search(r"\breturn\b", code):
        new.append("        return Task.CompletedTask;")
    lines[m.start] = sig
    lines[m.start + 2:m.end] = new


def _unfilled(m: TestMethod, reason: str) -> str:
    why = one_line(reason)
    if len(why) > REASON_LIMIT:
        why = why[:REASON_LIMIT - 3].rstrip() + "..."
    return (f"Assert.Fail({cs_string(f'no test body for {m.label}: {why}')});\n"
            "return Task.CompletedTask;")


def _type_index(solution: GeneratedSolution) -> dict[str, set[str]]:
    """Type name to the namespaces that declare it, over the solution's C# files."""
    index: dict[str, set[str]] = {}
    for f in solution.files:
        if not f.path.endswith(".cs"):
            continue
        ns = _NAMESPACE.search(f.content)
        if not ns:
            continue
        for t in _TYPE_DECL.findall(f.content):
            index.setdefault(t, set()).add(ns.group(1))
    return index


def _add_usings(text: str, bodies: list[str], index: dict[str, set[str]]) -> str:
    """Add ``using`` lines for types the new bodies name that have exactly one namespace."""
    lines = text.split("\n")
    own = _NAMESPACE.search(text)
    present = {ln.strip()[6:-1] for ln in lines if re.match(r"^using [\w.]+;$", ln.strip())}
    words = set(re.findall(r"\b[A-Z]\w*\b", code_only("\n".join(bodies))))
    wanted = sorted({
        next(iter(index[w])) for w in words
        if len(index.get(w, ())) == 1 and next(iter(index[w])) != (own.group(1) if own else "")
    } - present)
    if not wanted:
        return text
    at = max((i for i, ln in enumerate(lines) if ln.startswith("using ")), default=-1) + 1
    lines[at:at] = [f"using {ns};" for ns in wanted]
    return "\n".join(lines)


# ------------------------------------------------------------ prompt context


def _rule_targets(rules: list[Rule], methods: list[TestMethod]) -> list[Rule]:
    ids = {m.ref for m in methods if m.kind == "rule"}
    return [r for r in rules if r.id in ids]


def _activities(process: Process, rules: list[Rule], methods: list[TestMethod]) -> list[str]:
    acts = [c.activity_id for r in rules for c in r.citations]
    for m in methods:
        if m.kind == "branch":
            acts += [x for t in process.transitions if t.id == m.ref for x in (t.from_id, t.to_id)]
    return list(dict.fromkeys(acts))


def _targets_block(f: GenFile, methods: list[TestMethod], process: Process,
                   rules: list[Rule]) -> str:
    by_rule = {r.id: r for r in rules}
    lines = f.content.split("\n")
    out = [f"File: {f.path}"]
    for m in methods:
        out.append(f"- method {m.name} ({m.kind} {m.ref})")
        if m.kind == "rule" and m.ref in by_rule:
            out.append(f"  rule: {by_rule[m.ref].text}")
        if m.kind == "branch":
            for t in process.transitions:
                if t.id == m.ref:
                    out.append(f"  branch: {t.from_id} to {t.to_id} ({t.kind}): {t.expression or 'otherwise'}")
        out += ["  current method:", *("  " + ln for ln in lines[m.start - 4:m.end + 1])]
    return "\n".join(out)


def _signatures(solution: GeneratedSolution, process: Process) -> str:
    """Public declarations of the process's service, controller and consumer, and of the test
    helpers (TestDatabase, Support)."""
    stem = ident(process.name, "Process")
    wanted = {f"{stem}{s}.cs" for s in (*DRIVEN_SUFFIXES, "State", "Job")}
    out: list[str] = []
    for f in solution.files:
        name = PurePosixPath(f.path).name
        helper = is_test_path(f.path) and (name == "TestDatabase.cs" or "/Support/" in f.path)
        mine = not is_test_path(f.path) and name in wanted and f.process_id in (None, process.id)
        if not (helper or mine):
            continue
        out.append(f"=== {f.path}")
        ns = _NAMESPACE.search(f.content)
        if ns:
            out.append(f"namespace {ns.group(1)};")
        for ln in strip_comments(f.content).split("\n"):
            s = ln.strip()
            if re.match(r"^(?:public|internal)\b", s) and not s.startswith("public const"):
                out.append("  " + s.rstrip("{").rstrip())
    return "\n".join(out) or "(none)"


def _scenarios_block(solution: GeneratedSolution, process: Process,
                     scenarios: list[Scenario]) -> str:
    stem = ident(process.name, "Process")
    files = {f.path: f.content for f in solution.files}
    out: list[str] = []
    for s in scenarios:
        if s.process_id != process.id:
            continue
        rel = tests_gen.fixture_path(process, s)
        fixture = next((c for p, c in files.items() if p.endswith("/" + rel)), None)
        out.append(
            f"- scenario {s.id} ({s.kind}): {s.name}; load with "
            f'ScenarioFixture.Load("{stem}", "{tests_gen.scenario_stem(process, s)}") from {rel}'
        )
        if fixture:
            out.append(fixture[:FIXTURE_TEXT_LIMIT])
        else:
            expected = s.expected.model_dump(mode="json") if s.expected else None
            out.append(json.dumps({"input": s.input, "expected": expected}, ensure_ascii=False,
                                  default=str)[:FIXTURE_TEXT_LIMIT])
    return "\n".join(out) or "(no scenarios)"


# ------------------------------------------------------------ agent


def _parse(data: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in data.get("tests") or []:
        if isinstance(item, dict) and isinstance(item.get("method"), str) \
                and isinstance(item.get("body"), str):
            out[item["method"].strip()] = item["body"]
    return out


async def _fill_file(
    f: GenFile,
    solution: GeneratedSolution,
    process: Process,
    rules: list[Rule],
    scenarios: list[Scenario],
    memory_items: list[MemoryItem],
    provider: Any,
    rejections: list[BuildRejection] | None,
) -> GenFile | None:
    stem = ident(process.name, "Process")
    methods = placeholder_methods(f.content)
    targeted = _rule_targets(rules, methods)
    system, user = render_prompt(
        "test_engineer",
        process_name=process.name,
        source=render_process(focused_process(process, _activities(process, targeted, methods)),
                              []),
        rules=rules_block(targeted),
        targets=_targets_block(f, methods, process, rules),
        files=_scenarios_block(solution, process, scenarios),
        design=_signatures(solution, process),
        memory=memory_block(memory_items),
    )
    accepted: dict[str, str] = {}
    reasons: dict[str, str] = {}
    pending = [m.name for m in methods]
    for attempt in (1, 2):
        prompt = user
        if attempt == 2:
            prompt = (f"{user}\n\nYour previous bodies for these methods were rejected by the "
                      "checks. The reasons, as data:\n"
                      + wrap_source("\n".join(f"- {n}: {reasons[n]}" for n in pending))
                      + "\nReturn bodies for these methods only.")
        try:
            data, _ = await ask_json(
                provider, "test", process, system, prompt, TEST_SCHEMA,
                tags={"stage": "test", "process": process.id, "file": f.path},
            )
            bodies = _parse(data)
        except JsonOutputError as exc:
            bodies = {}
            for n in pending:
                reasons[n] = f"the reply was not valid JSON ({exc})"
        else:
            for n in pending:
                problem = (check_test_body(bodies[n], stem) if n in bodies
                           else "no body was returned for this method")
                if problem:
                    reasons[n] = problem
                else:
                    accepted[n] = bodies[n]
                    reasons.pop(n, None)
        for n in pending:
            if n in reasons:
                log.warning("test engineer rejected %s::%s (attempt %d): %s", f.path, n, attempt,
                            mask_sensitive(reasons[n]))
                if rejections is not None:
                    rejections.append(BuildRejection(path=f"{f.path}::{n}", attempt=attempt,
                                                     reason=reasons[n]))
        pending = [n for n in pending if n not in accepted]
        if not pending:
            break

    lines = f.content.split("\n")
    for m in sorted(methods, key=lambda x: x.start, reverse=True):  # bottom up keeps indices
        body = accepted.get(m.name) or _unfilled(m, reasons.get(m.name, "rejected"))
        _splice(lines, m, body)
    text = "\n".join(lines)
    if accepted:
        text = _add_usings(text, list(accepted.values()), _type_index(solution))
    if len(_TEST_ATTR.findall(code_only(text))) != len(_TEST_ATTR.findall(code_only(f.content))):
        log.warning("test engineer dropped %s: the number of tests changed", f.path)
        return None
    if text == f.content:
        return None
    return f.model_copy(update={"content": text})


async def fill_test_bodies(
    solution: GeneratedSolution,
    process: Process,
    rules: list[Rule],
    scenarios: list[Scenario],
    design: DesignRecord,
    memory_items: list[MemoryItem],
    provider: Any,
    rejections: list[BuildRejection] | None = None,
) -> list[GenFile]:
    """Write the placeholder bodies of ``<Process>RulesTests.cs`` and ``<Process>BranchTests.cs``.

    One model call per file (task kind ``test``) and one retry for the bodies code rejected.
    Accepted bodies replace their placeholder and nothing else changes except ``async`` on a
    method that now awaits and ``using`` lines for generated types the bodies name. A body still
    rejected stays ``Assert.Fail("no test body for <id>: <reason>")``. Returns only the test files
    whose content changed; ``solution`` is not modified. ``design`` is accepted for the common
    agent signature; ownership comes from ``GenFile.process_id`` and the file names."""
    del design  # the test classes and their ids come from the files themselves
    out: list[GenFile] = []
    for f in _test_files(solution, process):
        changed = await _fill_file(f, solution, process, rules, scenarios, memory_items, provider,
                                   rejections)
        if changed is not None:
            out.append(changed)
    return out
