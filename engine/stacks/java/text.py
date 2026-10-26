"""Java source text helpers: literals, comments, text blocks and import order."""
from __future__ import annotations

import re

PLACEHOLDER = 'throw new UnsupportedOperationException("agent fills: {what}");'


def one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _no_unicode_escape(text: str) -> str:
    """Java reads ``\\uXXXX`` as a character everywhere in a source file, comments included:
    a TIBCO expression quoting one must not turn into a line break. ``\\\\u`` is not an escape."""
    return re.sub(r"\\(?=u)", r"\\\\", text)


def java_string(text: str) -> str:
    """A Java string literal (``"a\\"b"``); non-ASCII characters are kept (the source is UTF-8)."""
    out = []
    for c in str(text):
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20 or c in "  ":
            out.append(f"\\u{ord(c):04x}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def comment(text: str) -> str:
    """Text safe inside a ``//`` comment: one line, no unicode escape."""
    return _no_unicode_escape(one_line(text))


def doc(text: str) -> str:
    """Text safe inside a ``/** */`` comment: one line, no ``*/``, no unicode escape, HTML escaped."""
    t = comment(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return t.replace("*/", "*&#47;").replace("@", "&#64;")


def tibco(target: str, expression: str, pad: str) -> str:
    """The ``// TIBCO: <target> = <expression>`` line a mapping keeps for every binding."""
    return f"{pad}// TIBCO: {comment(target)} = {comment(expression)}"


def text_block(text: str, pad: str) -> str:
    """A Java text block holding ``text`` exactly (backslashes and triple quotes escaped), each line
    indented by ``pad``; the closing delimiter at ``pad`` keeps the text's own indentation."""
    body = str(text).replace("\r\n", "\n").replace("\\", "\\\\").replace('"""', '\\"""')
    lines = body.split("\n")
    # trailing spaces are stripped from text blocks; \s keeps one where the TIBCO text has it
    lines = [re.sub(r" $", r"\\s", ln) if ln.endswith(" ") else ln for ln in lines]
    inner = "\n".join(f"{pad}{ln}" if ln else "" for ln in lines)
    return f'"""\n{inner}\n{pad}"""'


_IMPORT = re.compile(r"^import (static )?[\w.]+(\.\*)?;$")


def sort_imports(text: str) -> str:
    """The import block in the order palantir-java-format writes it: static imports first, then the
    others, each ASCII sorted, duplicates dropped."""
    lines = text.split("\n")
    idx = [i for i, ln in enumerate(lines) if _IMPORT.match(ln)]
    if not idx:
        return text
    first, last = idx[0], idx[-1]
    block = lines[first:last + 1]
    if any(ln and not _IMPORT.match(ln) for ln in block):
        return text  # something between imports: leave it alone
    imports = sorted({ln for ln in block if ln})
    static = [ln for ln in imports if ln.startswith("import static ")]
    plain = [ln for ln in imports if not ln.startswith("import static ")]
    out = static + ([""] if static and plain else []) + plain
    return "\n".join(lines[:first] + out + lines[last + 1:])
