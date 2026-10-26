"""The test-body check shared by the Java, Go and Python stacks (spec section 3).

The .NET stack keeps its deep C# analysis (``engine.agents.test_engineer.check_test_body``); the
other stacks refuse a model-written test body here, by plain text rules, and leave the rest to the
run itself (a body that does not compile or fails is not kept) and to the test reviewer.

A body is refused when it still holds the ``no test body`` placeholder, sleeps, opens a socket or
a file itself (the generated test database and fixture helpers do that), is longer than
``MAX_LINES`` lines, or has no assertion on a value under test: an assertion of the stack whose
checked expression names something other than a literal (``assert True``, ``assertThat(true)``
and a ``t.Fatal`` under ``if false`` do not count, and in Go a ``t.Fatal``/``t.Errorf`` counts
only under an ``if`` whose condition names a value).
"""
from __future__ import annotations

import re

MAX_LINES = 120

PLACEHOLDER_LEFT = "placeholder still present: write the test body"
SLEEPS = "sleeps are not allowed: wait on the result, never on the clock"
OPENS_IO = ("opens a socket or a file: use the generated test database and fixture helpers")
TOO_LONG = f"the body is longer than {MAX_LINES} lines"
NO_ASSERTION = "no assertion on a value under test"

_PLACEHOLDER = re.compile(r"no test body for\b")
_SLEEP = re.compile(r"\bThread\s*\.\s*sleep\s*\(|\btime\s*\.\s*[Ss]leep\s*\(|\basyncio\s*\.\s*sleep\s*\(")
_IO: dict[str, re.Pattern[str]] = {
    "java": re.compile(r"\bnew\s+(?:Server)?Socket\s*\(|\bnew\s+File(?:Input|Output)?(?:Stream|Reader|Writer)?\s*\("
                       r"|\bFiles\s*\.\s*\w+\s*\(|\bPaths\s*\.\s*get\s*\(|\bRandomAccessFile\b"),
    "go": re.compile(r"\bnet\s*\.\s*(?:Dial|Listen)\w*\s*\(|\bos\s*\.\s*(?:Open|OpenFile|Create|ReadFile|WriteFile)\s*\("
                     r"|\bioutil\s*\.\s*\w+\s*\("),
    "python": re.compile(r"(?<![\w.])open\s*\(|\bsocket\s*\.\s*\w+\s*\(|\.\s*(?:read|write)_(?:text|bytes)\s*\("
                         r"|\bos\s*\.\s*open\s*\("),
}
_LITERAL_WORDS = frozenset({"true", "false", "null", "nil", "None", "True", "False"})
_WORD = re.compile(r"\b[A-Za-z_]\w*\b")
_NUMBER_WORD = re.compile(r"^\d")


def _blank(text: str, stack: str) -> str:
    """``text`` with comments removed and string contents blanked (quotes kept), same length."""
    out: list[str] = []
    i, n = 0, len(text)
    hash_comment = stack == "python"
    while i < n:
        c = text[i]
        if hash_comment and c == "#" or not hash_comment and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if not hash_comment and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
            continue
        triple = text[i:i + 3]
        if triple in ('"""', "'''") and stack in ("python", "java"):
            j = text.find(triple, i + 3)
            j = n if j == -1 else j + 3
            out.append(triple + re.sub(r"[^\n]", " ", text[i + 3:max(i + 3, j - 3)]) + text[max(i + 3, j - 3):j])
            i = j
            continue
        if c == "`" and stack == "go":
            j = text.find("`", i + 1)
            j = n if j == -1 else j + 1
            out.append("`" + re.sub(r"[^\n]", " ", text[i + 1:j - 1]) + text[j - 1:j])
            i = j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(c + " " * max(0, j - i - 2) + (text[j - 1] if j - i >= 2 else ""))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _names_a_value(expr: str) -> bool:
    """True when ``expr`` (code only) names something other than a literal."""
    return any(w not in _LITERAL_WORDS and not _NUMBER_WORD.match(w) for w in _WORD.findall(expr))


def _args(code: str, open_at: int) -> str:
    """The text between the parenthesis at ``open_at`` and its match."""
    depth = 0
    for k in range(open_at, len(code)):
        if code[k] == "(":
            depth += 1
        elif code[k] == ")":
            depth -= 1
            if depth == 0:
                return code[open_at + 1:k]
    return code[open_at + 1:]


_JAVA_ASSERT = re.compile(r"\b(?:assertThat|assertEquals|assertNotEquals|assertTrue|assertFalse|assertThrows"
                          r"|assertNull|assertNotNull|assertArrayEquals|assertIterableEquals)\s*\(")
_GO_FAIL = re.compile(r"\bt\s*\.\s*(?:Errorf|Fatalf|Fatal|Error)\s*\(")
_GO_IF = re.compile(r"\bif\s+([^{]*)\{")
_PY_ASSERT = re.compile(r"(?m)^\s*assert\b(.*)$")
_PY_RAISES = re.compile(r"\bpytest\s*\.\s*raises\s*\(")


def _open_at(code: str, start: int, at: int) -> bool:
    """Whether the block opened just before ``start`` is still open at ``at``."""
    depth = 1
    for c in code[start:at]:
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth == 0:
            return False
    return True


def _asserts_a_value(code: str, stack: str) -> bool:
    if stack == "java":
        return any(_names_a_value(_args(code, m.end() - 1)) for m in _JAVA_ASSERT.finditer(code))
    if stack == "go":
        for m in _GO_FAIL.finditer(code):
            guard = None
            for g in _GO_IF.finditer(code, 0, m.start()):
                if _open_at(code, g.end(), m.start()):
                    guard = g  # the nearest if whose block still holds the call
            if guard is not None and _names_a_value(guard.group(1)):
                return True
        return False
    if stack == "python":
        if any(_names_a_value(m.group(1).split(",", 1)[0]) for m in _PY_ASSERT.finditer(code)):
            return True
        return any(_names_a_value(_args(code, m.end() - 1)) for m in _PY_RAISES.finditer(code))
    raise KeyError(f"no generic test-body check for stack {stack!r}")


def check_generic_test_body(body: str, stack: str) -> str | None:
    """Why ``body`` may not become a test body of a ``stack`` test (``java``, ``go``, ``python``),
    or ``None`` when it may. ``KeyError`` for a stack these rules do not cover."""
    if stack not in _IO:
        raise KeyError(f"no generic test-body check for stack {stack!r}")
    if _PLACEHOLDER.search(body):
        return PLACEHOLDER_LEFT
    code = _blank(body, stack)
    if _SLEEP.search(code):
        return SLEEPS
    if _IO[stack].search(code):
        return OPENS_IO
    if len(body.strip("\n").splitlines()) > MAX_LINES:
        return TOO_LONG
    if not _asserts_a_value(code, stack):
        return NO_ASSERTION
    return None
