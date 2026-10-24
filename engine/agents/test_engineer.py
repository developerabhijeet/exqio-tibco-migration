"""Test Engineer: writes the bodies of the generated rule and branch tests.

``tests_gen`` writes ``<Process>RulesTests.cs`` (one test per approved rule) and
``<Process>BranchTests.cs`` (one test per branch) with ``Assert.Fail("no test body for ...")`` where
no body could be generated. The model writes those bodies; plain code checks each one
(:func:`check_test_body`), splices it into its method and nothing else, retries a rejected body
once, and leaves a body still rejected as ``Assert.Fail("no test body for <id>: <reason>")`` so gate
G4 stays blocked for an honest reason.
"""
from __future__ import annotations

import bisect
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
DRIVEN_SUFFIXES = ("Service", "Controller", "Consumer", "Job")
FIXTURE_TEXT_LIMIT = 3000
REASON_LIMIT = 160

_PLACEHOLDER = re.compile(r'Assert\.Fail\(\s*"no test body for ([^"]*)"\s*\)')
_SIGNATURE = re.compile(r"^    public (?:async )?Task (\w+)\(\)\s*$")
_TRAIT = re.compile(r'\[Trait\("(Rule|Branch)",\s*"([^"]*)"\)\]')
# matched against _tight(code): whitespace around ".", "(", ")" and "," removed
_FAIL = re.compile(r"\bAssert\.Fail\(")
_TRIVIAL = re.compile(r"\bAssert\.(?:True\(true|False\(false)[,)]")
# an Assert call in code (any whitespace): the method name, then the "(" that opens the arguments
_ASSERT_AT = re.compile(r"\bAssert\s*\.\s*(\w+)\s*(?:<[^;()]*?>\s*)?\(")
_COMPARISON = re.compile(r"==|!=|<=|>=|\s<\s|\s>\s")
_NUMBER = re.compile(r"^-?\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?[mMdDfFuUlL]*$")
_ENUM_MEMBER = re.compile(r"^[A-Z]\w*(?:\.[A-Z]\w*)*\.([A-Z]\w*)$")
# Asserts that compare a value with an expected one; for these only the leading arguments
# are compared (a precision, comparer or message argument is not an expected value).
VALUE_ASSERTS: dict[str, int] = {
    "Equal": 2, "NotEqual": 2, "StrictEqual": 2, "NotStrictEqual": 2, "Equivalent": 2,
    "Contains": 2, "DoesNotContain": 2, "StartsWith": 2, "EndsWith": 2, "Matches": 2,
    "DoesNotMatch": 2, "InRange": 3, "NotInRange": 3,
}
# the exception type argument is the expected value
THROW_ASSERTS = frozenset({"Throws", "ThrowsAsync", "ThrowsAny", "ThrowsAnyAsync"})
# Asserts whose single argument is checked against an implied expected value
UNARY_ASSERTS: dict[str, str] = {"Null": "null", "Empty": "<empty>", "Single": "<one element>"}
# Asserts that run an Assert per element of a collection, through a lambda parameter
ELEMENT_ASSERTS = frozenset({"All", "Collection"})
EXPECT_HINT = ("assert the expected value: Assert.Equal(<expected>, <value from the call>), "
               "Assert.True/False(<condition on the call result>), "
               "Assert.Empty/Single/Null(<value from the call>)")
# a leading declaration or assignment: "var x =", "T x =", "using T x =" or "x ="
_BINDING = re.compile(r"^\s*(?:(?:var|[\w<>?,\[\]\s]+)\s+)?(\w+)\s*=(?![=>])")
# a leading tuple deconstruction: "var (a, b) =" or "(var a, var b) =" or "(a, b) ="
_DECONSTRUCTION = re.compile(r"^\s*(?:var\s*)?\(([\w\s,]*)\)\s*=(?![=>])")
_LAMBDA_PARAMS = re.compile(r"^\s*(?:async\s+)?(?:\(([\w\s,]*)\)|(\w+))\s*=>")
_NULL_CHECK = re.compile(r"\bis\s+(?:not\s+)?null\b")
_PRESENCE = re.compile(r"\.(ContainsKey|Any)\s*\(")
_COMPLETED_RETURN = re.compile(r"\breturn\b[^;]*;")
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


# A statement can open with block syntax: a closing brace, "try {", "finally {", or a header such
# as "using (var database = new TestDatabase())" followed by "{". Splitting on ";" alone leaves
# that syntax glued to the front of the first statement in the block, where the binding and call
# patterns no longer match it. A body that wrapped its work in try/finally or a using block was
# therefore read as calling nothing at all and rejected with "the body does not call the generated
# <service>", which is how a correct test ended up as an Assert.Fail placeholder that blocks G4.
_BLOCK_LEAD = re.compile(
    r"^\s*(?:[{}]\s*"
    r"|(?:try|finally|else|do|unsafe|checked|unchecked)\s*"
    r"|(?:if|for|foreach|while|lock|using|switch|fixed)\s*"
    r"\((?P<head>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*)")


def _unblock(statement: str) -> list[str]:
    """``statement`` with its leading block syntax removed, and any binding that syntax held.

    ``using (var database = new TestDatabase()) { var factory = new ApiFactory(database);``
    becomes the two statements it stands for, so both bindings are seen."""
    out: list[str] = []
    rest = statement
    while True:
        m = _BLOCK_LEAD.match(rest)
        if m is None:
            break
        head = (m.groupdict().get("head") or "").strip()
        if head and "=" in head:
            out.append(f"{head};")
        rest = rest[m.end():]
    out.append(rest)
    return out


def _statements(body: str) -> list[str]:
    """Statements of ``body`` (comments removed; split on ``;`` outside literals, then on the
    block syntax that can precede a statement)."""
    stripped = strip_comments(body)
    code = code_only(body)
    out: list[str] = []
    start = 0
    for k, ch in enumerate(code):
        if ch == ";":
            out.extend(_unblock(stripped[start:k + 1]))
            start = k + 1
    if stripped[start:].strip():
        out.extend(_unblock(stripped[start:]))
    return [st for st in out if st.strip()]


# ApiFactory hosts the generated API in memory (WebApplicationFactory<Program>), so a body that
# drives the process through it exercises the generated controller, service and repository for
# real. The prompt tells the model to use ApiFactory, so a check that insisted on a direct call to
# the service rejected every body written that way: the prompt and the gate have to agree.
SCAFFOLD_DRIVERS = ("ApiFactory",)
# A branch test may also call the process's generated condition class. A branch is a condition, and
# that class is where code put it, so calling it tests exactly the thing the test is named after.
# Demanding that every branch be reached end to end is a demand the solution cannot always meet: a
# link no scenario takes, a defensive "otherwise", a fault path. Those bodies were rejected, stayed
# Assert.Fail placeholders, and blocked G4 with no way for any model to unblock it. Rule tests stay
# strict: a rule is about the behavior of the process, so its test drives the process.
BRANCH_DRIVERS = ("Conditions",)


def _driven_types(stem: str, kind: str = "rule") -> list[str]:
    extra = [f"{stem}{s}" for s in BRANCH_DRIVERS] if kind == "branch" else []
    return [f"{stem}{s}" for s in DRIVEN_SUFFIXES] + extra + list(SCAFFOLD_DRIVERS)


def _tight(code: str) -> str:
    """``code`` with the whitespace around ``.``, ``(``, ``)`` and ``,`` removed, so that
    ``Assert . Fail ( "x" )`` reads as ``Assert.Fail("x")``."""
    return re.sub(r"\s*([.(),])\s*", r"\1", code)


def _close(code: str, open_at: int) -> int:
    """Index of the bracket closing the one at ``open_at`` in ``code`` (literals blanked), or
    ``len(code)`` when it is not closed."""
    depth = 0
    for k in range(open_at, len(code)):
        if code[k] in "([{":
            depth += 1
        elif code[k] in ")]}":
            depth -= 1
            if depth == 0:
                return k
    return len(code)


def _arguments(text: str, code: str, open_at: int) -> list[str]:
    """The top-level arguments of the call whose ``(`` is at ``open_at``; ``code`` is ``text``
    with literals blanked (same length)."""
    end = _close(code, open_at)
    out: list[str] = []
    depth, start = 0, open_at + 1
    for k in range(open_at + 1, end):
        if code[k] in "([{":
            depth += 1
        elif code[k] in ")]}":
            depth -= 1
        elif code[k] == "," and depth == 0:
            out.append(text[start:k])
            start = k + 1
    out.append(text[start:end])
    return [a.strip() for a in out if a.strip()]


def _comparison(text: str) -> tuple[str, str, str] | None:
    """``(left, operator, right)`` of the first top-level comparison in ``text``, if any."""
    code = code_only(text)
    depth = 0
    for k, ch in enumerate(code):
        depth += (ch in "([{") - (ch in ")]}")
        if depth:
            continue
        m = _COMPARISON.match(code, k)
        if m:
            return text[:k].strip(), m.group(0).strip(), text[m.end():].strip()
    return None


def _binding(cs: str) -> tuple[list[str], int]:
    """The names a statement (literals blanked) binds by a leading declaration, assignment or
    tuple deconstruction, and where its right-hand side starts; ``([], 0)`` when it binds
    nothing. An Assert statement never binds a name (an object initializer inside it is an
    expected value)."""
    if cs.lstrip().startswith("Assert"):
        return [], 0
    m = _DECONSTRUCTION.match(cs)
    if m:
        names = [p.split()[-1] for p in m.group(1).split(",") if p.strip()]
        return names, m.end()
    m = _BINDING.match(cs)
    if m:
        return [m.group(1)], m.end()
    return [], 0


def _unwrap(arg: str) -> str:
    """``arg`` without the parentheses that enclose all of it."""
    a = arg.strip()
    while a.startswith("(") and _close(code_only(a), 0) == len(a) - 1:
        a = a[1:-1].strip()
    return a


def _top_level_operands(text: str, ops: tuple[str, ...]) -> list[str]:
    """``text`` split at the top-level occurrences of ``ops`` (``||`` and ``&&``)."""
    code = code_only(text)
    out: list[str] = []
    depth, start, k = 0, 0, 0
    while k < len(code):
        ch = code[k]
        depth += (ch in "([{") - (ch in ")]}")
        if depth == 0 and any(code.startswith(op, k) for op in ops):
            out.append(text[start:k])
            k += 2
            start = k
            continue
        k += 1
    out.append(text[start:])
    return out


def _presence_check(cond: str) -> str | None:
    """``ContainsKey`` or ``Any`` when the condition is a bare ``.ContainsKey(...)`` or ``.Any()``
    call (presence, not a value); ``None`` otherwise."""
    c = code_only(cond).rstrip()
    last = None
    for m in _PRESENCE.finditer(c):
        last = m
    if last is None or _close(c, last.end() - 1) != len(c) - 1:
        return None
    if last.group(1) == "ContainsKey" or not c[last.end():-1].strip():
        return last.group(1)
    return None


def _lambda_blocks(code: str) -> list[tuple[int, int]]:
    """``(open, close)`` of every ``=> { ... }`` lambda block in ``code`` (literals blanked)."""
    stack: list[tuple[int, bool]] = []
    out: list[tuple[int, int]] = []
    for k, ch in enumerate(code):
        if ch == "{":
            stack.append((k, code[:k].rstrip().endswith("=>")))
        elif ch == "}" and stack:
            start, is_lambda = stack.pop()
            if is_lambda:
                out.append((start, k))
    return out


def _completed_returns(code: str) -> list[tuple[int, int, bool]]:
    """``(start, end, exact)`` of each return statement outside a lambda block that names
    ``Task.CompletedTask``; ``exact`` when it is ``return Task.CompletedTask;`` and nothing
    else (the form :func:`_splice` rewrites to ``return;``)."""
    blocks = _lambda_blocks(code)
    out: list[tuple[int, int, bool]] = []
    for m in _COMPLETED_RETURN.finditer(code):
        if "CompletedTask" not in m.group(0) or any(a < m.start() < b for a, b in blocks):
            continue
        exact = re.sub(r"\s+", "", m.group(0)) == "returnTask.CompletedTask;"
        out.append((m.start(), m.end(), exact))
    return out


@dataclass
class _BodyCheck:
    reason: str | None
    expected: list[str]  # the expected-value arguments of the accepted Assert calls


def _analyse(body: str, stem: str, kind: str = "rule") -> _BodyCheck:
    """The checks behind :func:`check_test_body`, keeping the expected values it found."""
    code = code_only(body)
    if not code.strip():
        return _BodyCheck("the body is empty", [])
    for open_, close in ("{}", "()"):
        depth = 0
        for ch in code:
            depth += (ch == open_) - (ch == close)
            if depth < 0:
                break
        if depth != 0:
            return _BodyCheck("the body has unbalanced braces or parentheses", [])
    tight = _tight(code)
    if _FAIL.search(tight):
        return _BodyCheck("the body still calls Assert.Fail", [])
    if _TRIVIAL.search(tight):
        return _BodyCheck("the body asserts a constant; it must assert the behavior", [])
    if _TEST_ATTR.search(code):
        return _BodyCheck("the body adds a [Fact] or [Theory] attribute", [])
    if _PRAGMA.search(code):
        return _BodyCheck("the body introduces #pragma warning disable", [])
    stripped = strip_comments(body)  # same length as code
    if _OUTSIDE.search(stripped):
        return _BodyCheck("the body references a file outside the solution", [])
    for start, end, exact in _completed_returns(code):
        if not exact:
            return _BodyCheck(
                "the body returns Task.CompletedTask inside an expression "
                f"({one_line(stripped[start:end])}), which does not compile in an async test "
                "method; use a plain return", [])
    types = _driven_types(stem, kind)
    type_alt = "|".join(re.escape(t) for t in types)
    statements = [(st, code_only(st)) for st in _statements(body)]

    # A variable holds an instance when its right-hand side is the construction itself:
    # "new T(...)" (with an optional initializer), "...<T>()" or a "(T)" cast, optionally
    # followed by "!", or when it is declared with the type. When a call follows the
    # construction ("new T(x).ExecuteAsync(...)") the variable holds the call's result.
    new_rhs = re.compile(rf"^new\s+(?:{type_alt})\s*\(")
    factory_rhs = re.compile(rf"<\s*(?:{type_alt})\s*>\s*\(\s*\)$")
    cast_rhs = re.compile(rf"^\(\s*(?:{type_alt})\s*\)")

    def instance(rhs: str) -> bool:
        r = rhs.strip().rstrip(";").rstrip().rstrip("!").rstrip()
        if cast_rhs.match(r) or factory_rhs.search(r):
            return True
        m = new_rhs.match(r)
        if m is None:
            return False
        rest = r[_close(r, m.end() - 1) + 1:].lstrip()
        if rest.startswith("{"):
            rest = rest[_close(rest, 0) + 1:].lstrip()
        return rest.rstrip("!").strip() == ""

    bound: set[str] = set()
    constructed: set[str] = set()  # built in the body with "new"
    for _, cs in statements:
        m = re.search(rf"\b(?:{type_alt})\s+(\w+)\s*=(?![=>])", cs)
        names, rhs = _binding(cs)
        if m:
            bound.add(m.group(1))
        elif names and instance(cs[rhs:]):
            bound.update(names)
        elif len(names) == 1 and re.match(r"^\s*new\b", cs[rhs:]):
            constructed.add(names[0])
    receivers = "|".join([*types, *(re.escape(b) for b in bound)])
    receiver = (rf"(?:\b(?:{receivers})|\bnew\s+(?:{type_alt})\s*\([^;]*?\)(?:\s*\{{[^;]*?\}})?"
                rf"|<\s*(?:{type_alt})\s*>\s*\(\s*\))")
    call = re.compile(rf"{receiver}\s*\.\s*\w+\s*[<(]")
    calling = [cs for _, cs in statements if call.search(cs)]
    if not calling:
        return _BodyCheck(f"the body does not call the generated {', '.join(types)}", [])

    def derive(names: set[str]) -> None:
        """Add the names bound from a right-hand side that uses one of ``names``."""
        for _ in range(3):
            for _, cs in statements:
                new_names, rhs = _binding(cs)
                if new_names and names and not (set(new_names) <= names | bound) \
                        and re.search(rf"\b(?:{'|'.join(map(re.escape, names))})\b", cs[rhs:]):
                    names.update(n for n in new_names if n not in bound)

    results: set[str] = set()
    for cs in calling:
        results.update(n for n in _binding(cs)[0] if n not in bound)
    derive(results)  # values derived from a result count as results
    # State built in the body and passed to the generated type or the generated call: its
    # value after the call is the call's effect, so an Assert on it looks at the call.
    passed = re.compile(rf"(?:\bnew\s+(?:{type_alt})|{receiver}\s*\.\s*\w+\s*(?:<[^;()]*?>)?)\s*\(")
    effects: set[str] = set()
    for _, cs in statements:
        for m in passed.finditer(cs):
            inside = cs[m.end():_close(cs, m.end() - 1)]
            effects.update(constructed & set(re.findall(r"\b\w+\b", inside)))
    derive(effects)
    effects -= results
    result_ref = re.compile(rf"\b(?:{'|'.join(map(re.escape, results))})\b") if results else None
    effect_ref = re.compile(rf"\b(?:{'|'.join(map(re.escape, effects))})\b") if effects else None
    element: set[str] = set()  # lambda parameters of an Assert.All/Collection over a result

    def dependent(arg: str) -> bool:
        c = code_only(arg)
        return bool(call.search(c) or (result_ref and result_ref.search(c))
                    or (element and re.search(rf"\b(?:{'|'.join(map(re.escape, element))})\b", c)))

    def effect(arg: str) -> bool:
        return bool(effect_ref and effect_ref.search(code_only(arg)))

    def independent(arg: str) -> bool:
        c = code_only(arg).strip()
        return bool(c) and not dependent(arg) and c not in ("null", "default", "default!") \
            and c not in bound

    expected: list[str] = []
    weak: list[str] = []
    selfish: list[str] = []
    for st, cs in statements:
        element.clear()
        for m in _ASSERT_AT.finditer(cs):
            name = m.group(1)
            args = _arguments(st, cs, m.end() - 1)
            if not any(dependent(a) or effect(a) for a in args):
                continue  # does not look at the call result
            if name in THROW_ASSERTS and "<" in cs[m.start():m.end()]:
                expected.append(re.sub(r"\s+", "", cs[m.start():m.end()]).split("<", 1)[1][:-2])
                continue
            if name in ELEMENT_ASSERTS:
                if dependent(args[0]) or effect(args[0]):
                    for a in args[1:]:
                        p = _LAMBDA_PARAMS.match(a)
                        if p:
                            element.update((p.group(1) or p.group(2) or "").replace(",", " ").split())
                continue  # the Asserts inside the lambdas are checked through the parameter
            if name in ("True", "False"):
                cond = _unwrap(args[0]) if args else ""
                parts = [code_only(p).strip() for p in _top_level_operands(cond, ("||", "&&"))]
                if any(p in ("true", "false") for p in parts):
                    return _BodyCheck(
                        f"Assert.{name}({one_line(cond)}) joins a constant with || or &&, so it "
                        f"asserts a constant; it must assert the behavior: {EXPECT_HINT}", [])
                cmp = _comparison(cond)
                if cmp is None:
                    if _NULL_CHECK.search(code_only(cond)):
                        weak.append(f"Assert.{name}(x is null)")
                        continue
                    presence = _presence_check(cond)
                    if presence:
                        weak.append(f"Assert.{name}(x.{presence}(...))")
                        continue
                    operands = [cond, name.lower()]  # a condition on the result is compared with true/false
                else:
                    left, op, right = cmp
                    if op in ("==", "!=") and "null" in (code_only(left), code_only(right)):
                        weak.append(f"Assert.{name}(x {op} null)")
                        continue
                    operands = [left, right]
            elif name in UNARY_ASSERTS:
                operands = [args[0], UNARY_ASSERTS[name]]
            elif name in VALUE_ASSERTS:
                operands = args[:VALUE_ASSERTS[name]]
                codes = [code_only(a).strip() for a in operands]
                if any(c.startswith("typeof") for c in codes) and any(c.endswith(".GetType()") for c in codes):
                    weak.append(f"Assert.{name}(typeof(T), x.GetType())")
                    continue
                if "null" in codes:
                    if name in ("Equal", "StrictEqual", "Equivalent"):
                        operands = [a for a in operands if code_only(a).strip() != "null"] + ["null"]
                    else:
                        weak.append(f"Assert.{name}(null, x)")
                        continue
            else:
                weak.append(f"Assert.{name}")
                continue
            # the expected value an Assert implies (null, empty, one element, true/false)
            implied = [a for a in operands if a in (*UNARY_ASSERTS.values(), "null", "true", "false")]
            if any(dependent(a) for a in operands):
                found = implied + [a for a in operands if independent(a) and a not in implied]
            elif any(effect(a) for a in operands):
                found = implied + [a for a in operands
                                   if independent(a) and not effect(a) and a not in implied]
            else:
                continue
            if found:
                expected += found
            else:
                selfish.append(f"Assert.{name}")
    if expected:
        return _BodyCheck(None, expected)
    if selfish:
        return _BodyCheck(
            f"{selfish[0]} compares values that all come from the generated call, so it cannot "
            "fail; one argument must be the expected value (a literal, a named constant or a "
            f"value from the scenario fixture): {EXPECT_HINT}", [])
    if weak:
        return _BodyCheck(
            f"{weak[0]} does not compare the result with an expected value (presence, type, "
            f"identity and null checks do not count): {EXPECT_HINT}", [])
    return _BodyCheck(f"no Assert checks a value returned by the generated call; {EXPECT_HINT}",
                      [])


def check_test_body(body: str, stem: str, json_kinds: dict[str, str] | None = None,
                    kind: str = "rule") -> str | None:
    """Why ``body`` may not become a test body, or ``None`` when it may.

    The body must call a method of ``<stem>Service``, ``<stem>Controller`` or ``<stem>Consumer``
    (through a variable bound to an instance of that type, on a ``new`` instance or statically).
    At least one value-comparing Assert (``Equal``, ``Contains``, ``Assert.True(a == b)``, an
    expected exception type, ...) must have an argument that uses the call result (or state
    built in the body and passed to the generated type or call) and an argument that does not:
    a literal, a named constant or a value from the scenario fixture. ``Assert.True/False`` of a
    condition on the result compares it with ``true``/``false``; ``Null``, ``Empty``, ``Single``
    and ``Equal(null, x)`` imply their expected value; an Assert inside an ``All``/``Collection``
    lambda counts through the lambda parameter. ``NotNull``, ``NotEmpty``, ``IsType`` (also as
    ``Equal(typeof(T), x.GetType())``), ``IsAssignableFrom``, ``Same``, ``NotSame``, null checks,
    bare ``ContainsKey``/``Any()`` and a condition joined with a ``true``/``false`` literal do not
    count. The body must contain no ``Assert.Fail(``, ``Assert.True(true)``, ``[Fact]``/
    ``[Theory]``, ``#pragma warning disable``, file reference outside the solution, unbalanced
    braces (whitespace around ``.`` and ``(`` does not matter) or a ``return`` of
    ``Task.CompletedTask`` inside an expression (it cannot become ``return;``).

    ``json_kinds`` maps a field of the process's JSON contracts to the JSON type it carries
    (:func:`_contracts_block`). A body that reads one of those fields as a C# type the JSON cannot
    produce is rejected: it compiles and throws when the test runs, which looks like a failure of
    the process and is a defect in the test."""
    reason = _analyse(body, stem, kind).reason
    if reason is not None:
        return reason
    return json_read_problem(body, json_kinds or {})


# ``node["field"].GetValue<T>()`` and ``(T)node["field"]``: both throw when T is not the JSON type.
_JSON_GET = re.compile(r'\[\s*"([^"]+)"\s*\]\s*[!?]*\s*(?:\.\s*AsValue\(\)\s*[!?]*\s*)?'
                       r'\.\s*GetValue\s*<\s*([\w.]+\??)\s*>')
_JSON_CAST = re.compile(r'\(\s*(string|bool|int|long|short|decimal|double|float)\s*\?*\s*\)\s*'
                        r'[\w.]*\s*[!?]*\s*\[\s*"([^"]+)"\s*\]')


def json_read_problem(body: str, json_kinds: dict[str, str]) -> str | None:
    """Why a body reads a contract field as a type its JSON cannot produce, or ``None``."""
    code = strip_comments(body)  # not code_only: the field name is inside a string literal
    reads = [(f, t) for f, t in _JSON_GET.findall(code)] + [(f, t) for t, f in _JSON_CAST.findall(code)]
    for field, cs in reads:
        carried = json_kinds.get(field)
        asked = json_kind(cs)
        if carried is None or asked is None or asked == carried:
            continue
        helper = "ScenarioAssert.Number" if carried == "number" else "ScenarioAssert.Text"
        return (f'the response field "{field}" is a JSON {carried}, so reading it as {cs} throws '
                f"when the test runs; read it with {helper}(...) or with the C# type the contract "
                f"declares")
    return None


def _literal_found(arg: str, context: str) -> bool | None:
    """Whether the literal ``arg`` appears in ``context``; ``None`` when ``arg`` is not a literal
    that can be looked up (a fixture value, a named constant, a bool, a type)."""
    a = _unwrap(arg)
    if _NUMBER.match(a):
        want = a.rstrip("mMdDfFuUlL").replace("_", "")
        try:
            value = float(want)
        except ValueError:
            return None
        return any(float(n) == value for n in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", context))
    if len(a) >= 2 and a[0] in "\"'" and a[-1] == a[0] and code_only(a) == a[0] + " " * (len(a) - 2) + a[0]:
        return a[1:-1] in context
    m = _ENUM_MEMBER.match(a)
    if m and not re.search(r"\b(?:fixture|expected)\b", a, re.IGNORECASE):
        return re.search(rf"\b{re.escape(m.group(1))}\b", context, re.IGNORECASE) is not None
    return None


def expected_value_warning(body: str, stem: str, context: str, kind: str = "rule") -> str | None:
    """A warning when the expected literals of an accepted ``body`` appear nowhere in
    ``context`` (the rule text and citation, or the branch expression, and the fixtures).

    Soft: a derived value (input 2 plus the rule's "+ 1" gives 3) is legitimate, so this never
    rejects a body; it flags it for a person. ``None`` when the body is rejected anyway, when an
    expected value is not a literal (a fixture value or a named constant) or when a literal is
    found."""
    check = _analyse(body, stem, kind)
    if check.reason is not None or not check.expected:
        return None
    literals: list[str] = []
    for arg in check.expected:
        found = _literal_found(arg, context)
        if found is None or found:
            return None
        literals.append(arg.strip())
    return (f"the expected value {', '.join(literals)} does not appear in the rule text, its "
            "citation, the branch expression or the fixture; check that it is the expected value "
            "the rule gives for this input")


# ------------------------------------------------------------ splicing


def _indent(body: str) -> list[str]:
    text = textwrap.dedent(body.replace("\r\n", "\n").expandtabs(4)).strip("\n")
    return [("        " + ln) if ln.strip() else "" for ln in text.split("\n")]


def _plain_returns(new: list[str]) -> list[str]:
    """``new`` with every ``return Task.CompletedTask;`` outside a lambda block rewritten to
    ``return;`` (comments and literals are left alone; a line comment is dropped from the code
    that is scanned, so columns before it are the same in both)."""
    codes = [code_only(ln) for ln in new]
    offsets = [0]
    for c in codes[:-1]:
        offsets.append(offsets[-1] + len(c) + 1)
    out = list(new)
    for start, end, exact in reversed(_completed_returns("\n".join(codes))):
        if not exact:
            continue
        i = bisect.bisect_right(offsets, start) - 1
        j = bisect.bisect_right(offsets, end - 1) - 1
        out[i:j + 1] = [out[i][:start - offsets[i]] + "return;" + out[j][end - offsets[j]:]]
    return out


def _splice(lines: list[str], m: TestMethod, body: str, placeholder: bool = False) -> None:
    """Replace the body of ``m`` in place.

    An accepted body always becomes an ``async Task`` method (xUnit accepts it): a body that
    does not await starts with ``await Task.CompletedTask;``, no return is ever appended, and
    every ``return Task.CompletedTask;`` outside a lambda block becomes ``return;`` (dropped
    when it is the last line). The ``[Fact]``/``[Theory]`` attributes and the method name are
    left alone. A ``placeholder`` body (``Assert.Fail`` then ``return Task.CompletedTask;``)
    keeps a plain ``Task`` signature."""
    new = _indent(body)
    sig = lines[m.start]
    if placeholder:
        sig = sig.replace("public async Task ", "public Task ")
    else:
        if "public async Task " not in sig:
            sig = sig.replace("public Task ", "public async Task ")
        new = _plain_returns(new)
        while new and new[-1].strip() == "return;":
            new.pop()
        if not re.search(r"\bawait\b", code_only("\n".join(new))):
            new.insert(0, "        await Task.CompletedTask;")
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


def _expectation_text(m: TestMethod, process: Process, rules: list[Rule]) -> str:
    """What the test of ``m`` should expect: the rule text and citations, or the branch."""
    if m.kind == "rule":
        return rules_block([r for r in rules if r.id == m.ref], include_rejected=True)
    return "\n".join(f"{t.from_id} to {t.to_id} ({t.kind}): {t.expression or 'otherwise'}"
                     for t in process.transitions if t.id == m.ref)


# ------------------------------------------------- the JSON the response actually carries
# A test body reads the response as JSON, so it has to know which JSON type each field carries.
# Nothing told it: a body read principalBalance, a decimal, with GetValue<string>(), which compiles
# and throws "An element of type 'Number' cannot be converted to a 'System.String'" at run time.
# That failure says nothing about the process under test, and no repair loop may touch a test, so
# the whole build failed on a defect in the probe. The contracts already state every type, so the
# shape is given to the writer and the same shape is checked against what it wrote.
_JSON_FIELD = re.compile(
    r'\[JsonPropertyName\("([^"]+)"\)\](?:\s*\[[^\]]*\])*\s*public\s+(\S+)\s+\w+\s*\{')
_RECORD_DECL = re.compile(r"^\s*public\s+(?:sealed\s+)?(?:record|class)\s+(\w+)", re.MULTILINE)
_CS_NUMERIC = frozenset({"int", "long", "short", "byte", "sbyte", "uint", "ulong", "ushort",
                         "decimal", "double", "float"})
_CS_TEXT = frozenset({"string", "DateOnly", "TimeOnly", "DateTime", "DateTimeOffset", "Guid",
                      "TimeSpan", "char"})


def json_kind(cs_type: str) -> str | None:
    """The JSON type a C# member is serialized as, or ``None`` when it is not one of the scalars."""
    t = cs_type.strip().rstrip("?").strip()
    if t in _CS_NUMERIC:
        return "number"
    if t == "bool":
        return "boolean"
    if t in _CS_TEXT:
        return "string"
    return None


def _contracts_block(solution: GeneratedSolution, process: Process) -> tuple[str, dict[str, str]]:
    """The contract records this process uses as JSON, and ``{json field: json type}``.

    A field whose name is used with two different JSON types is left out of the map: the writer
    still sees both records, but the check cannot say which one a body means."""
    used: set[str] = set()
    for f in solution.files:
        if not is_test_path(f.path) and f.path.endswith(".cs") and f.process_id == process.id:
            used.update(re.findall(r"\b[A-Z]\w*\b", code_only(f.content)))
    out: list[str] = []
    kinds: dict[str, str] = {}
    clash: set[str] = set()
    for f in sorted(solution.files, key=lambda x: x.path):
        if "/Contracts/" not in f.path or not f.path.endswith(".cs"):
            continue
        shown = False
        for record, body in _records(f.content):
            if record not in used:
                continue
            fields = [(name, cs, json_kind(cs)) for name, cs in _JSON_FIELD.findall(body)]
            if not fields:
                continue
            if not shown:
                out.append(f"=== {f.path}")
                shown = True
            out.append(f"record {record}, as it appears in the JSON body:")
            for name, cs, kind in fields:
                out.append(f'  "{name}": {kind or "object"} (C# {cs})')
                if kind is None:
                    continue
                if kinds.setdefault(name, kind) != kind:
                    clash.add(name)
    for name in clash:
        kinds.pop(name, None)
    return "\n".join(out) or "(none)", kinds


def _records(text: str) -> list[tuple[str, str]]:
    """``(name, text)`` of each top-level record or class declaration in a generated file."""
    starts = [(m.group(1), m.start()) for m in _RECORD_DECL.finditer(text)]
    return [(name, text[at:(starts[i + 1][1] if i + 1 < len(starts) else len(text))])
            for i, (name, at) in enumerate(starts)]


def _signatures(solution: GeneratedSolution, process: Process) -> str:
    """Public declarations of the process's service, controller and consumer, and of the test
    helpers (TestDatabase, Support)."""
    stem = ident(process.name, "Process")
    # ProcessResult is what every ExecuteAsync returns, so every test body asserts on it; it was
    # not in this list, and a test that guesses at a member of it ("result.Result") does not
    # compile, which stops the whole suite rather than failing one case. ErrorReply is the body of
    # a 4xx reply, read just as often.
    wanted = {f"{stem}{s}.cs" for s in (*DRIVEN_SUFFIXES, "State", "Job")} | {"ProcessResult.cs"}
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


BODY_KEPT = 1600
"""Characters of a rejected body kept in the build record and sent back on the retry."""


def _rejected_body(body: str) -> str:
    """A rejected body, masked and bounded, for the record and for the retry prompt."""
    text = mask_sensitive(body.strip())
    if len(text) <= BODY_KEPT:
        return text
    return f"{text[:BODY_KEPT]}\n... ({len(text) - BODY_KEPT} more characters)"


def _retry_note(name: str, reason: str, body: str) -> str:
    kept = _rejected_body(body)
    if not kept:
        return f"- {name}: {reason}\n  (no body was returned)"
    return f"- {name}: {reason}\n  the body that was rejected:\n{textwrap.indent(kept, '    ')}"


async def _fill_file(
    f: GenFile,
    solution: GeneratedSolution,
    process: Process,
    rules: list[Rule],
    scenarios: list[Scenario],
    memory_items: list[MemoryItem],
    provider: Any,
    rejections: list[BuildRejection] | None,
    warnings: list[dict[str, str]] | None,
) -> GenFile | None:
    stem = ident(process.name, "Process")
    methods = placeholder_methods(f.content)
    targeted = _rule_targets(rules, methods)
    fixtures = _scenarios_block(solution, process, scenarios)
    contracts, json_kinds = _contracts_block(solution, process)
    system, user = render_prompt(
        "test_engineer",
        process_name=process.name,
        source=render_process(focused_process(process, _activities(process, targeted, methods)),
                              []),
        rules=rules_block(targeted),
        targets=_targets_block(f, methods, process, rules),
        files=fixtures,
        design=_signatures(solution, process),
        contracts=contracts,
        memory=memory_block(memory_items),
    )
    accepted: dict[str, str] = {}
    reasons: dict[str, str] = {}
    last: dict[str, str] = {}  # the body each pending method was last rejected for
    by_name = {m.name: m for m in methods}
    pending = [m.name for m in methods]
    for attempt in (1, 2):
        prompt = user
        if attempt == 2:
            # The reason alone leaves the model guessing at what it did: a model told only "the
            # body does not call the generated service" writes the same body again, which is how
            # five rules arrive with no body at all. It gets back what it wrote, beside the reason.
            prompt = (f"{user}\n\nYour previous bodies for these methods were rejected by the "
                      "checks. Each one below is the reason and the body it was given for. Fix "
                      "what the reason names; do not start from something unrelated.\n"
                      + wrap_source("\n\n".join(_retry_note(n, reasons[n], last.get(n, ""))
                                                for n in pending))
                      + "\nReturn bodies for these methods only.")
        try:
            data, _ = await ask_json(
                provider, "test", process, system, prompt, TEST_SCHEMA,
                tags={"stage": "test", "process": process.id, "file": f.path},
                # The retry goes to the standard tier. The efficient model is right for a first
                # pass, and a second pass on the same model reproduces the same misunderstanding;
                # the cost of one stronger call is far below a build that fails G4 on empty bodies.
                tier="standard" if attempt == 2 else None,
            )
            bodies = _parse(data)
        except JsonOutputError as exc:
            bodies = {}
            for n in pending:
                reasons[n] = f"the reply was not valid JSON ({exc})"
        else:
            for n in pending:
                problem = (check_test_body(bodies[n], stem, json_kinds, by_name[n].kind)
                           if n in bodies else "no body was returned for this method")
                last[n] = bodies.get(n, "")
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
                                                     reason=reasons[n],
                                                     body=_rejected_body(last.get(n, ""))))
        pending = [n for n in pending if n not in accepted]
        if not pending:
            break

    if warnings is not None:
        for m in methods:
            if m.name not in accepted:
                continue
            why = expected_value_warning(accepted[m.name], stem,
                                         f"{_expectation_text(m, process, rules)}\n{fixtures}",
                                         m.kind)
            if why:
                log.warning("test engineer warning %s::%s: %s", f.path, m.name, mask_sensitive(why))
                warnings.append({"path": f.path, "method": m.name, "reason": why})

    lines = f.content.split("\n")
    for m in sorted(methods, key=lambda x: x.start, reverse=True):  # bottom up keeps indices
        if m.name in accepted:
            _splice(lines, m, accepted[m.name])
        else:
            _splice(lines, m, _unfilled(m, reasons.get(m.name, "rejected")), placeholder=True)
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
    warnings: list[dict[str, str]] | None = None,
) -> list[GenFile]:
    """Write the placeholder bodies of ``<Process>RulesTests.cs`` and ``<Process>BranchTests.cs``.

    One model call per file (task kind ``test``) and one retry for the bodies code rejected.
    Accepted bodies replace their placeholder and nothing else changes except ``async`` on a
    method that now awaits and ``using`` lines for generated types the bodies name. A body still
    rejected stays ``Assert.Fail("no test body for <id>: <reason>")``. Returns only the test files
    whose content changed; ``solution`` is not modified. ``design`` is accepted for the common
    agent signature; ownership comes from ``GenFile.process_id`` and the file names.

    ``rejections`` collects the rejected replies; ``warnings`` collects ``{path, method, reason}``
    for accepted bodies whose expected literal appears nowhere in the rule text, citation,
    branch expression or fixtures (:func:`expected_value_warning`), for a person to check."""
    del design  # the test classes and their ids come from the files themselves
    out: list[GenFile] = []
    for f in _test_files(solution, process):
        changed = await _fill_file(f, solution, process, rules, scenarios, memory_items, provider,
                                   rejections, warnings)
        if changed is not None:
            out.append(changed)
    return out
