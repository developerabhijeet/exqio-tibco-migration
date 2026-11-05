"""``PythonLanguage``: the agents' code-text checks for Python source (target-stacks spec section 3).

``strip_comments`` and ``code_only`` are ``tokenize``-based (a comment token removed, a string
token's contents blanked); text ``tokenize`` refuses (a fragment, an unfinished fill) goes through a
small lexer with the same rules. ``declarations`` are the ``def``, ``async def`` and ``class``
headers with their annotations, joined across lines (``ruff format`` wraps long ones), so a
formatted file still declares what its unformatted scaffold declared.
"""
from __future__ import annotations

import io
import re
import tokenize

from engine.stacks.base import ForbiddenPattern
from engine.stacks.generic_checks import check_generic_test_body
from engine.stacks.python.text import py_string

PLACEHOLDER = re.compile(r'NotImplementedError\(\s*"agent fills:')
_TIBCO_COMMENT = re.compile(r"^#\s*TIBCO\b")

# What a Code Builder fill of a production file may not introduce (builder.check_filled_file).
_ASSERTION = re.compile(r"(?m)^\s*assert\b|\bpytest\s*\.|\bunittest\b")
_TEST_FUNCTION = re.compile(r"(?m)^\s*(?:async\s+)?def\s+test_\w*\s*\(|@pytest\.mark\b")
_SUPPRESS = re.compile(r"#\s*(?:noqa|type:\s*ignore|pragma:\s*no\s*cover)\b|\bwarnings\s*\.\s*(?:filterwarnings|simplefilter)\b")
# A handler that would swallow a TIBCO fault: a bare except, or one of Exception, BaseException,
# ProcessFault or a generated fault exception (their names end in Fault/FaultError).
_FAULT_CATCH = re.compile(r"\bexcept\s*(?::|\(?\s*(?:[\w.]+\s*,\s*)*(?:[\w.]*\.)?"
                          r"(?:Exception|BaseException|\w*Fault\w*)\b)")
_OUTSIDE = re.compile(
    r"""  ["'](?:[A-Za-z]:[\\/]|\\\\|~/|file:/
           |/(?:etc|home|usr|var|tmp|root|opt|mnt|proc|Users|Volumes)/)
      | (?:^|["'\s/\\])\.\.[\\/]""",
    re.VERBOSE | re.MULTILINE,
)
FORBIDDEN: tuple[ForbiddenPattern, ...] = (
    ForbiddenPattern(_ASSERTION, "the code contains an assertion (assert, pytest); assertions belong to tests",
                     in_code=True),
    ForbiddenPattern(_TEST_FUNCTION, "the code contains a test function or a pytest mark", in_code=True),
    ForbiddenPattern(_SUPPRESS, "the code introduces a warning or lint suppression (noqa, type: ignore, warnings)"),
    ForbiddenPattern(_FAULT_CATCH, "the code adds an except that would swallow a TIBCO fault; the fault handlers "
                                   "are generated from the TIBCO process, so fill the body of the one that is "
                                   "there (catch the specific exception a call raises)", in_code=True),
    ForbiddenPattern(_OUTSIDE, "the code references a file outside the project"),
)

_HEADER = re.compile(r"^\s*(?:async\s+def|def|class)\s+[A-Za-z_]\w*")
_STRING_START = re.compile(r"(?i)(?:rb|br|fr|rf|b|r|u|f)?(?:'''|\"\"\"|'|\")")


def _lex(text: str, blank_strings: bool) -> str:
    """The fallback lexer: comments removed (up to the newline), string contents optionally blanked
    (quotes and prefixes kept, newlines kept)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        m = _STRING_START.match(text, i) if c in "'\"rRbBuUfF" else None
        if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            quote = m.group(0).lstrip("rRbBuUfF")
            prefix = m.group(0)[: len(m.group(0)) - len(quote)]
            raw = "r" in prefix.lower()
            k = m.end()
            while k < n:
                if text[k] == "\\" and not raw:
                    k += 2
                    continue
                if text.startswith(quote, k):
                    break
                if text[k] == "\n" and len(quote) == 1:
                    break
                k += 1
            end = min(k + len(quote), n) if text.startswith(quote, k) else k
            inner = text[m.end():k]
            out.append(m.group(0) + (re.sub(r"[^\n]", " ", inner) if blank_strings else inner)
                       + text[k:end])
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _tokens(text: str, blank_strings: bool) -> str | None:
    """``text`` through ``tokenize``: comments removed, string contents blanked when asked; None when
    ``tokenize`` cannot read it."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln))
    edits: list[tuple[int, int, str]] = []

    def at(pos: tuple[int, int]) -> int:
        return offsets[pos[0] - 1] + pos[1]

    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                edits.append((at(tok.start), at(tok.end), ""))
            elif blank_strings and tok.type == tokenize.STRING:
                s = tok.string
                m = _STRING_START.match(s)
                q = len(m.group(0)) - len(m.group(0).lstrip("rRbBuUfF")) if m else 0
                quote_len = 3 if s[q:q + 3] in ('"""', "'''") else 1
                head, tail = q + quote_len, quote_len
                inner = re.sub(r"[^\n]", " ", s[head:len(s) - tail])
                edits.append((at(tok.start), at(tok.end), s[:head] + inner + s[len(s) - tail:]))
            elif blank_strings and tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                edits.append((at(tok.start), at(tok.end), re.sub(r"[^\n{}]", " ", tok.string)))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    out, last = [], 0
    for start, end, repl in sorted(edits):
        out.append(text[last:start])
        out.append(repl)
        last = end
    out.append(text[last:])
    return "".join(out)


def _norm(text: str) -> str:
    return re.sub(r"\s+\)", ")", re.sub(r"\(\s+", "(", " ".join(text.split()))).replace(",)", ")")


class PythonLanguage:
    comment_prefix = "#"
    import_word = "import"

    @property
    def forbidden(self) -> tuple[ForbiddenPattern, ...]:
        return FORBIDDEN

    @property
    def placeholder(self) -> re.Pattern[str]:
        return PLACEHOLDER

    def test_placeholder(self, test_id: str) -> str:
        return f"pytest.fail({py_string(f'no test body for {test_id}')})"

    def strip_comments(self, text: str) -> str:
        out = _tokens(text, blank_strings=False)
        return out if out is not None else _lex(text, blank_strings=False)

    def code_only(self, text: str) -> str:
        out = _tokens(text, blank_strings=True)
        return out if out is not None else _lex(text, blank_strings=True)

    def declarations(self, text: str) -> list[str]:
        """``def``/``async def``/``class`` headers with their annotations, up to the ``:`` that opens
        the body, one per header however many lines it spans; normalised (decorators not included)."""
        stripped = self.strip_comments(text)
        code = _lex(stripped, blank_strings=True)  # same length as ``stripped``
        lines, raw = code.split("\n"), stripped.split("\n")
        out: list[str] = []
        i = 0
        while i < len(lines):
            if not _HEADER.match(lines[i]):
                i += 1
                continue
            depth, parts, j = 0, [], i
            while j < len(lines):
                depth += lines[j].count("(") + lines[j].count("[") - lines[j].count(")") - lines[j].count("]")
                parts.append(raw[j])
                if depth <= 0 and lines[j].rstrip().endswith(":"):
                    break
                j += 1
            header = _norm(" ".join(parts)).rstrip()
            out.append(header[:-1].rstrip() if header.endswith(":") else header)
            i = j + 1
        return out

    def tibco_comments(self, text: str) -> list[str]:
        """The ``# TIBCO`` comments alone on their line, normalised (not inside a string)."""
        out: list[str] = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT and _TIBCO_COMMENT.match(tok.string) and not tok.line[:tok.start[1]].strip():
                    out.append(" ".join(tok.string.split()))
            return out
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
        code = _lex(text, blank_strings=True)
        for line, raw in zip(code.split("\n"), text.split("\n"), strict=False):
            body = raw.strip()
            if body.startswith("#") and _TIBCO_COMMENT.match(body) and line.strip() == "":
                out.append(" ".join(body.split()))
        return out

    def is_test_path(self, path: str) -> bool:
        p = path.replace("\\", "/")
        return p.startswith("tests/") or "/tests/" in p

    def check_test_body(self, body: str, stem: str, kind: str = "rule",
                        json_kinds: dict[str, str] | None = None) -> str | None:
        return check_generic_test_body(body, "python")


__all__ = ["FORBIDDEN", "PLACEHOLDER", "PythonLanguage"]
