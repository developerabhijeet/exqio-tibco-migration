"""``JavaLanguage``: the agents' code-text checks for Java source (target-stacks spec section 3).

A small C-like lexer (line and block comments, string and char literals with escapes, text blocks)
backs ``strip_comments`` and ``code_only``; ``declarations`` are the method, constructor, record,
class, interface and enum headers, joined across lines (palantir-java-format wraps long ones), so
a formatted file still declares what its unformatted scaffold declared.
"""
from __future__ import annotations

import re

from engine.stacks.base import ForbiddenPattern
from engine.stacks.generic_checks import check_generic_test_body
from engine.stacks.java.text import java_string

PLACEHOLDER = re.compile(r'UnsupportedOperationException\(\s*"agent fills:')
_TIBCO_COMMENT = re.compile(r"^//\s*TIBCO\b")

# What a Code Builder fill of a production file may not introduce (builder.check_filled_file).
_ASSERTION = re.compile(r"(?<![\w.$])(?:assert[A-Z]\w*|fail)\s*\(|\bAssertions\s*\.|\borg\.(?:junit|assertj)\b")
_TEST_ANNOTATION = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate|Disabled)\b")
_SUPPRESS = re.compile(r"@SuppressWarnings\b")
# A catch that would swallow a TIBCO fault: of ProcessFault or a generated fault exception, or of
# the unchecked roots they derive from. A checked exception a call declares (IOException,
# InterruptedException) may be caught and rethrown unchecked: Java signatures are fixed by the design.
_FAULT_CATCH = re.compile(r"\bcatch\s*\(\s*(?:final\s+)?(?:[\w.]+\s*\|\s*)*(?:[\w.]*\.)?"
                          r"(?:Exception|RuntimeException|Throwable|Error|\w*Fault\w*)\b")
# String literals holding an absolute filesystem path, a home path, a file URI or a
# parent-directory step (the C# rule without its #r/#load/#include directives, which Java has not).
_OUTSIDE = re.compile(
    r"""  "(?:[A-Za-z]:[\\/]|\\\\|~/|file:/
           |/(?:etc|home|usr|var|tmp|root|opt|mnt|proc|Users|Volumes)/)
      | (?:^|["\s/\\])\.\.[\\/]""",
    re.VERBOSE | re.MULTILINE,
)
FORBIDDEN: tuple[ForbiddenPattern, ...] = (
    ForbiddenPattern(_ASSERTION, "the code contains an assertion (assertThat, assertEquals, fail); assertions "
                                 "belong to tests", in_code=True),
    ForbiddenPattern(_TEST_ANNOTATION, "the code contains a @Test annotation", in_code=True),
    ForbiddenPattern(_SUPPRESS, "the code introduces @SuppressWarnings", in_code=True),
    ForbiddenPattern(_FAULT_CATCH, "the code adds a catch that would swallow a TIBCO fault; the fault handlers "
                                   "are generated from the TIBCO process, so fill the body of the one that is "
                                   "there (a checked exception a call declares may be caught and rethrown "
                                   "unchecked)", in_code=True),
    ForbiddenPattern(_OUTSIDE, "the code references a file outside the project"),
)

_MODIFIERS = ("public", "protected", "private", "static", "final", "abstract", "synchronized", "native",
              "default", "strictfp", "sealed", "non-sealed", "transient", "volatile")
_NOT_A_NAME = frozenset({"if", "for", "while", "switch", "catch", "synchronized", "try", "return", "new", "throw",
                         "else", "do", "case", "assert", "yield", "super", "this"})
_TYPE = r"[\w.$]+(?:\s*<.*>)?(?:\s*\[\s*\])*"
_MODS = r"(?:(?:" + "|".join(re.escape(m) for m in _MODIFIERS) + r")\s+)*"
_METHOD = re.compile(rf"^{_MODS}(?:<.+>\s+)?(?:(?P<type>{_TYPE})\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>.*)\)"
                     r"(?:\s*throws\s+[\w.,\s]+)?$")
_TYPE_DECL = re.compile(rf"^{_MODS}(?:class|interface|enum|record|@interface)\s+[A-Za-z_$][\w$]*\b")


def _literal_end(text: str, i: int) -> int:
    """Index just past the string, char literal or text block starting at ``i``."""
    n = len(text)
    if text.startswith('"""', i):
        k = i + 3
        while k < n:
            if text[k] == "\\":
                k += 2
                continue
            if text.startswith('"""', k):
                return k + 3
            k += 1
        return n
    quote = text[i]
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
        if c in "\"'":
            j = _literal_end(text, i)
            lit = text[i:j]
            if blank_literals:
                q = 3 if lit.startswith('"""') else 1
                closed = len(lit) >= 2 * q and lit.endswith(lit[:q])
                inner = lit[q:len(lit) - q] if closed else lit[q:]
                lit = lit[:q] + re.sub(r"[^\n]", " ", inner) + (lit[-q:] if closed else "")
            out.append(lit)
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


LINE_LIMIT = 120  # palantir-java-format's line length


def _line_comments(text: str) -> list[tuple[int, int, str]]:
    """``(line number, column, comment text)`` of every ``//`` comment that is alone on its line."""
    out: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            start = text.rfind("\n", 0, i) + 1
            if not text[start:i].strip():
                out.append((text.count("\n", 0, i), i - start, text[i:j].rstrip()))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'":
            i = _literal_end(text, i)
        else:
            i += 1
    return out


def _strip_annotations(text: str) -> str:
    """``text`` without ``@Name`` and ``@Name(...)`` annotations (balanced parentheses)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        m = re.compile(r"@(?!interface\b)[\w.$]+").match(text, i) if text[i] == "@" else None
        if m is None:
            out.append(text[i])
            i += 1
            continue
        j = m.end()
        k = j
        while k < n and text[k] in " \t\n":
            k += 1
        if k < n and text[k] == "(":
            depth = 0
            while k < n:
                depth += {"(": 1, ")": -1}.get(text[k], 0)
                k += 1
                if depth == 0:
                    break
            j = k
        out.append(" ")
        i = j
    return "".join(out)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _norm_header(text: str) -> str:
    """A header on one line, as a formatter's line breaks inside its parentheses leave it."""
    return re.sub(r"\s+\)", ")", re.sub(r"\(\s+", "(", _norm(text)))


def _is_header(segment: str, terminator: str) -> bool:
    shape = _norm(_strip_annotations(segment))
    if not shape:
        return False
    if terminator == "{" and _TYPE_DECL.match(shape):
        return True
    m = _METHOD.match(shape)
    if m is None or m.group("name") in _NOT_A_NAME:
        return False
    typ = m.group("type")
    if typ is not None and (typ in _NOT_A_NAME or typ in _MODIFIERS):
        return False
    if "=" in re.sub(r"\(.*\)", "", shape) or "->" in shape:
        return False
    if terminator == ";":  # an interface or abstract method: it must name a return type
        return typ is not None
    return typ is not None or m.group("name")[:1].isupper()  # a constructor has no type


class JavaLanguage:
    comment_prefix = "//"

    @property
    def forbidden(self) -> tuple[ForbiddenPattern, ...]:
        return FORBIDDEN

    @property
    def placeholder(self) -> re.Pattern[str]:
        return PLACEHOLDER

    def test_placeholder(self, test_id: str) -> str:
        return f"fail({java_string(f'no test body for {test_id}')});"

    def strip_comments(self, text: str) -> str:
        return _lex(text, blank_literals=False)

    def code_only(self, text: str) -> str:
        return _lex(text, blank_literals=True)

    def declarations(self, text: str) -> list[str]:
        """Method, constructor and type headers, normalised (annotations kept, ``{`` dropped), one
        per header however many lines it spans."""
        stripped = self.strip_comments(text)
        code = _lex(stripped, blank_literals=True)  # same length as ``stripped``
        out: list[str] = []
        start = 0
        for k, ch in enumerate(code):
            if ch not in "{};":
                continue
            segment = code[start:k]
            if ch in "{;" and _is_header(segment, ch):
                out.append(_norm_header(stripped[start:k]))
            start = k + 1
        return out

    def tibco_comments(self, text: str) -> list[str]:
        """The ``// TIBCO`` line comments, normalised (not inside a block comment or a literal).

        palantir-java-format wraps a line comment longer than the line limit onto ``//`` lines below
        it; such a continuation (a comment line at the same indentation whose first word would not
        have fitted on the line above) is joined back, so a formatted file keeps the comment its
        scaffold had."""
        out: list[str] = []
        lines = _line_comments(text)
        k = 0
        while k < len(lines):
            line_no, col, body = lines[k]
            k += 1
            if not _TIBCO_COMMENT.match(body):
                continue
            parts, prev_len = [body], col + len(body)
            while k < len(lines) and lines[k][0] == line_no + 1 and lines[k][1] == col:
                nxt = lines[k][2][2:].strip()
                first = nxt.split(" ", 1)[0] if nxt else ""
                if not first or _TIBCO_COMMENT.match(lines[k][2]) or prev_len + 1 + len(first) <= LINE_LIMIT:
                    break
                parts.append(nxt)
                line_no, prev_len = lines[k][0], lines[k][1] + len(lines[k][2])
                k += 1
            out.append(_norm(" ".join(parts)))
        return out

    def is_test_path(self, path: str) -> bool:
        p = path.replace("\\", "/")
        return p.startswith("src/test/") or "/src/test/" in p

    def check_test_body(self, body: str, stem: str, kind: str = "rule",
                        json_kinds: dict[str, str] | None = None) -> str | None:
        return check_generic_test_body(body, "java")


__all__ = ["FORBIDDEN", "PLACEHOLDER", "JavaLanguage"]
