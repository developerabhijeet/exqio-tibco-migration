"""Deterministic layers 1-4 of the ExiQoian pipeline: normalise, injection check, greeting and
off-topic pre-filter. No model, no I/O."""
import re
import unicodedata

from pydantic import BaseModel

from engine.exiqoian.lexicon import OFF_DOMAIN_PHRASES, OFF_DOMAIN_SINGLE, split_identifier, tokens
from engine.exiqoian.patterns import INJECTION_RULES, LEET, OVERRIDE_COMPACT
from engine.exiqoian.replies import Verdict
from engine.memory.search import query_terms

# Cyrillic and Greek look-alikes of Latin letters (lowercase; applied after casefold).
_HOMOGLYPHS = str.maketrans({
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h", "і": "i", "ї": "i", "ј": "j", "к": "k",
    "м": "m", "о": "o", "р": "p", "ѕ": "s", "т": "t", "у": "y", "х": "x", "ԁ": "d", "һ": "h",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
    "υ": "u", "χ": "x", "γ": "y",
})
# Upper-case look-alikes, mapped BEFORE casefold (a capital Nu casefolds to nu, which looks like v).
_UPPER_HOMOGLYPHS = str.maketrans({
    **{chr(g): l for g, l in zip(
        (0x391, 0x392, 0x395, 0x396, 0x397, 0x399, 0x39a, 0x39c, 0x39d, 0x39f, 0x3a1, 0x3a4, 0x3a5, 0x3a7),
        "ABEZHIKMNOPTYX", strict=True)},
    **{chr(c): l for c, l in zip(
        (0x410, 0x412, 0x415, 0x41a, 0x41c, 0x41d, 0x41e, 0x420, 0x421, 0x422, 0x425, 0x406, 0x408, 0x405),
        "ABEKMHOPCTXIJS", strict=True)},
})
# Other characters that hide inside a word: combining grapheme joiner and the Hangul fillers
# (Cf characters, tags and soft hyphen are stripped by category).
_FILLERS = dict.fromkeys(map(ord, (chr(0x34f), chr(0x115f), chr(0x1160), chr(0x3164), chr(0xffa0))))
_LINE_BREAKS = re.compile("[\r\v\f\x85" + chr(0x2028) + chr(0x2029) + "]+")
_SPACES = re.compile(r"[^\S\n]+")
_BLANKS = re.compile(r"\s*\n\s*")
# Runs of single letters split by separators ("i g n o r e", "i.g.n.o.r.e").
_SPLIT_LETTERS = re.compile(r"(?<![a-z])(?:[a-z][ \t._\-*]+){2,}[a-z](?![a-z])")
_SPLIT_SEP = re.compile(r"[ \t._\-*]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_B64_LINE = re.compile(r"[a-z0-9+/=]+")


def normalise(text: str) -> str:
    """Matching form only: NFKC, invisible and bidi control characters removed, casefold,
    look-alike letters mapped to Latin, runs of blanks collapsed (line breaks are kept as one
    ``\\n`` so line-start rules still work)."""
    t = unicodedata.normalize("NFKC", text).replace(chr(0x2019), "'").replace(chr(0x2018), "'")
    t = _LINE_BREAKS.sub("\n", t).translate(_FILLERS)
    t = "".join(c for c in t if unicodedata.category(c) != "Cf")
    t = t.translate(_UPPER_HOMOGLYPHS).casefold().translate(_HOMOGLYPHS)
    return _BLANKS.sub("\n", _SPACES.sub(" ", t)).strip()


def _deobfuscate(n: str) -> tuple[str, bool]:
    """Leet-mapped text with single-letter runs joined (the obfuscated_override view), and
    whether any run was joined."""
    joined = False

    def join(m: re.Match[str]) -> str:
        nonlocal joined
        joined = True
        return _SPLIT_SEP.sub("", m.group())
    return _SPLIT_LETTERS.sub(join, n.translate(LEET)), joined


def _join_base64_lines(n: str) -> str:
    """Consecutive lines made only of base64 characters are joined (a payload wrapped at 60-76
    characters); lines of prose are left alone."""
    out: list[str] = []
    prev_b64 = False
    for line in n.split("\n"):
        stripped = line.strip()
        is_b64 = bool(_B64_LINE.fullmatch(stripped))
        if is_b64 and prev_b64:
            out[-1] += stripped
        else:
            out.append(line)
        prev_b64 = is_b64
    return "\n".join(out)


def detect_injection(text: str) -> str | None:
    """Name of the first matching injection rule for ``text`` (normalised here), else None."""
    n = normalise(text)
    for name, pattern in INJECTION_RULES:
        if name == "obfuscated_override":
            d, joined = _deobfuscate(n)
            if d != n and pattern.search(d):
                return name
            # a phrase spelled out letter by letter has no word gaps left
            if joined and OVERRIDE_COMPACT.search(_NON_ALNUM.sub("", d)):
                return name
        elif name == "encoded_payload":
            if pattern.search(_join_base64_lines(n)):  # base64 wrapped across lines
                return name
        elif pattern.search(n):
            return name
    return None


_GREETING = re.compile(
    r"^(?:hi|hello|hey|hiya|howdy|yo|namaste|thanks|thank you|thx|ok|okay|help|"
    r"good (?:morning|afternoon|evening)|who are you|what are you|what do you do|what can you do|"
    r"what can you do for me|how can you help|how can you help me)(?: (?:there|exiqoian|everyone|you))?$")


def is_greeting(normalised: str) -> bool:
    """A message that is only a greeting, thanks or a capability question."""
    return bool(_GREETING.match(re.sub(r"[^\w\s]", "", normalised).strip()))


_ARITH_LEAD = re.compile(r"^(?:please\s+)?(?:what(?:'s|s| is)|calculate|solve|compute)\b\s*")
_ARITH = re.compile(r"^[\d\s+\-*/^().=x×÷%]+$")


def _is_arithmetic(n: str) -> bool:
    t = n.replace("?", " ").replace("% of", "%*").strip()
    while (stripped := _ARITH_LEAD.sub("", t, count=1)) != t:
        t = stripped.strip()
    return bool(_ARITH.match(t)) and any(c.isdigit() for c in t) and any(c in "+-*/^×÷%x" for c in t)


def _project_word(tok: str, lexicon: frozenset[str]) -> bool:
    return tok in lexicon or any(p in lexicon for p in split_identifier(tok))


def prefilter(normalised: str, lexicon: frozenset[str], *, has_context: bool = False) -> str | None:
    """``"arithmetic"``, ``"off_domain"``, ``"too_vague"`` or None. A project word (or an
    identifier part of one) overrides off_domain and too_vague; too_vague is skipped with context."""
    if _is_arithmetic(normalised):
        return "arithmetic"
    toks = tokens(normalised)
    has_project_word = any(_project_word(t, lexicon) for t in toks)
    if not has_project_word:
        joined = " " + " ".join(toks) + " "
        if any(t in OFF_DOMAIN_SINGLE for t in toks) or any(f" {p} " in joined for p in OFF_DOMAIN_PHRASES):
            return "off_domain"
        if not has_context and len(query_terms(normalised)) < 2:
            return "too_vague"
    return None


class GuardResult(BaseModel):
    verdict: Verdict | None = None  # None: continue to retrieval
    rule: str | None = None


def check(text: str, lexicon: frozenset[str], *, has_context: bool = False) -> GuardResult:
    """Layers 2, 3 and 4 in order; the first that decides ends it."""
    if rule := detect_injection(text):
        return GuardResult(verdict="injection", rule=rule)
    n = normalise(text)
    if is_greeting(n):
        return GuardResult(verdict="intro", rule="greeting")
    if rule := prefilter(n, lexicon, has_context=has_context):
        return GuardResult(verdict="off_topic", rule=rule)
    return GuardResult()
