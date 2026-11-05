"""``GoLanguage``: the agents' code-text checks for Go source (target-stacks spec section 3).

A small lexer (line and block comments, interpreted strings and runes with escapes, raw strings in
backticks, which may span lines) backs ``strip_comments`` and ``code_only``; ``declarations`` are
the ``func`` signatures (methods with their receiver) and ``type`` lines. gofmt never reflows a
comment, so a ``// TIBCO`` line stays one line.
"""
from __future__ import annotations

import re

from engine.stacks.base import ForbiddenPattern
from engine.stacks.generic_checks import check_generic_test_body
from engine.stacks.go.text import go_string

PLACEHOLDER = re.compile(r'panic\(\s*"agent fills:')
_TIBCO_COMMENT = re.compile(r"^//\s*TIBCO\b")

# What a Code Builder fill of a production file may not introduce (builder.check_filled_file).
_ASSERTION = re.compile(r"\bt\s*\.\s*(?:Fatal|Fatalf|Error|Errorf|Fail|FailNow)\s*\(|\btesting\s*\.")
_TEST_FUNC = re.compile(r"\bfunc\s+(?:Test|Benchmark|Fuzz|Example)[A-Z_]\w*\s*\(")
_SUPPRESS = re.compile(r"//\s*nolint\b|//go:build\s+ignore\b|//go:linkname\b")
# What would swallow a TIBCO fault: a recover, or a new handler that tests for a fault (the fault
# handlers are generated from the TIBCO process).
_FAULT_CATCH = re.compile(r"\brecover\s*\(\s*\)|\btib\s*\.\s*(?:FaultNamed|AsFault)\s*\(")
# String literals holding an absolute filesystem path, a home path, a file URI or a
# parent-directory step.
_OUTSIDE = re.compile(
    r"""  ["`](?:[A-Za-z]:[\\/]|\\\\|~/|file:/
           |/(?:etc|home|usr|var|tmp|root|opt|mnt|proc|Users|Volumes)/)
      | (?:^|["`\s/\\])\.\.[\\/]""",
    re.VERBOSE | re.MULTILINE,
)
FORBIDDEN: tuple[ForbiddenPattern, ...] = (
    ForbiddenPattern(_ASSERTION, "the code contains an assertion (t.Fatal, t.Errorf, the testing package); "
                                 "assertions belong to tests", in_code=True),
    ForbiddenPattern(_TEST_FUNC, "the code contains a test function (func Test...)", in_code=True),
    ForbiddenPattern(_SUPPRESS, "the code introduces a //nolint, //go:build ignore or //go:linkname directive"),
    ForbiddenPattern(_FAULT_CATCH, "the code adds a recover or a fault test that would swallow a TIBCO fault; the "
                                   "fault handlers are generated from the TIBCO process, so fill the body of the one "
                                   "that is there (return the error)", in_code=True),
    ForbiddenPattern(_OUTSIDE, "the code references a file outside the project"),
)


def _literal_end(text: str, i: int) -> int:
    """Index just past the string, rune or raw string starting at ``i``."""
    n = len(text)
    quote = text[i]
    if quote == "`":
        j = text.find("`", i + 1)
        return n if j < 0 else j + 1
    k = i + 1
    while k < n:
        c = text[k]
        if c == "\\":
            k += 2
            continue
        if c == quote or c == "\n":
            return k + 1 if c == quote else k
        k += 1
    return n


def _lex(text: str, blank_literals: bool) -> str:
    """Comments removed (a block comment blanked, newlines kept; a line comment dropped up to its
    newline); with ``blank_literals`` the contents of literals blanked too, quotes kept."""
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
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
            continue
        if c in "\"'`":
            j = _literal_end(text, i)
            lit = text[i:j]
            if blank_literals:
                closed = len(lit) >= 2 and lit.endswith(lit[0])
                inner = lit[1:len(lit) - 1] if closed else lit[1:]
                lit = lit[0] + re.sub(r"[^\n]", " ", inner) + (lit[-1] if closed else "")
            out.append(lit)
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _line_comments(text: str) -> list[str]:
    """The text of every ``//`` comment that is alone on its line."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            start = text.rfind("\n", 0, i) + 1
            if not text[start:i].strip():
                out.append(text[i:j].rstrip())
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'`":
            i = _literal_end(text, i)
        else:
            i += 1
    return out


def _norm(text: str) -> str:
    return " ".join(text.split())


_FUNC = re.compile(r"^func\b")
_TYPE = re.compile(r"^type\s+[A-Za-z_]\w*\b")


class GoLanguage:
    comment_prefix = "//"
    import_word = "import"

    @property
    def forbidden(self) -> tuple[ForbiddenPattern, ...]:
        return FORBIDDEN

    @property
    def placeholder(self) -> re.Pattern[str]:
        return PLACEHOLDER

    def test_placeholder(self, test_id: str) -> str:
        return f"t.Fatal({go_string(f'no test body for {test_id}')})"

    def strip_comments(self, text: str) -> str:
        return _lex(text, blank_literals=False)

    def code_only(self, text: str) -> str:
        return _lex(text, blank_literals=True)

    def declarations(self, text: str) -> list[str]:
        """``func`` signatures (up to their body's ``{``) and ``type`` lines, normalised, one per
        declaration however many lines its parameter list spans."""
        stripped = self.strip_comments(text)
        code = _lex(stripped, blank_literals=True)  # same length as ``stripped``
        out: list[str] = []
        lines = code.split("\n")
        raw = stripped.split("\n")
        offsets = []
        pos = 0
        for ln in lines:
            offsets.append(pos)
            pos += len(ln) + 1
        for k, ln in enumerate(lines):
            s = ln.strip()
            if _TYPE.match(s):
                end = s.find("{")
                out.append(_norm(raw[k].strip()[: end if end >= 0 else len(s)]).rstrip())
                continue
            if not _FUNC.match(s):
                continue
            start = offsets[k] + (len(ln) - len(ln.lstrip()))
            depth, j = 0, start
            while j < len(code):
                ch = code[j]
                if ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "{" and depth == 0:
                    break
                elif ch == "\n" and depth == 0 and not code[start:j].rstrip().endswith(","):
                    break  # a signature without a body (an interface or external function)
                j += 1
            out.append(re.sub(r"\(\s+", "(", re.sub(r",?\s+\)", ")", _norm(stripped[start:j]))).rstrip())
        return out

    def tibco_comments(self, text: str) -> list[str]:
        """The ``// TIBCO`` line comments, normalised (not inside a block comment or a literal)."""
        return [_norm(c) for c in _line_comments(text) if _TIBCO_COMMENT.match(c)]

    def is_test_path(self, path: str) -> bool:
        p = path.replace("\\", "/")
        return p.startswith("tests/") or "/tests/" in p or p.endswith("_test.go")

    def check_test_body(self, body: str, stem: str, kind: str = "rule",
                        json_kinds: dict[str, str] | None = None) -> str | None:
        return check_generic_test_body(body, "go")


__all__ = ["FORBIDDEN", "PLACEHOLDER", "GoLanguage"]
