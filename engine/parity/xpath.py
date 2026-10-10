"""XPath 2.0 evaluation with BusinessWorks variable semantics (design spec section 6.2).

BW expressions address process variables as ``$Start/...`` and ``$ActivityName/...``; each
variable is the root element of that activity's output (or the process input). They are bound
here as ``elementpath`` variables holding lxml elements.

BW-specific functions are registered as *external functions* on a per-context
``XPath2Parser`` instance (``parser.external_function``), so each context closes over its own
substitution variables and clock. Implemented subset (all the estate uses plus the date
helpers an SME is likely to add):

* ``bw:getModuleProperty(name)``   value from ``substvars``; ``KeyError`` when undefined
* ``tib:trim(s)``                  whitespace trim
* ``tib:render-xml(node)``         serialise a node to an XML string
* ``tib:parse-dateTime(fmt, s)``   Java ``SimpleDateFormat`` pattern to ``xs:dateTime``
* ``tib:format-dateTime(fmt, dt)`` ``xs:dateTime`` to string with a Java pattern
* ``tib:current-dateTime()``       injectable clock (``now=``) for reproducible results
* ``tib:timestamp()``              the same clock, as an ISO-8601 string

The ``tib:`` namespace URI is the one the estate declares; the ``bw:`` URI is unverified (see
``BW_NS``). Both are overridable through ``namespaces``.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

from elementpath import XPath2Parser
from elementpath import XPathContext as _EPContext
from elementpath.datatypes import DateTime10
from lxml import etree

# TIB_NS is the URI the estate declares (LegacyAddressStandardize.process and 24 other files).
TIB_NS = "http://www.tibco.com/bw/xslt/custom-functions"
# UNVERIFIED: no file in the estate or the public samples declares an xmlns:bw= prefix, so
# this URI is from memory of BW6 documentation; nothing depends on it except the prefix.
BW_NS = "http://www.tibco.com/bw/xpath/bw-custom-functions"

DEFAULT_NAMESPACES = {
    "bw": BW_NS,
    "tib": TIB_NS,
    "xsd": "http://www.w3.org/2001/XMLSchema",
}


# --- Java SimpleDateFormat patterns --------------------------------------------

_JAVA_TOKEN = re.compile(r"'([^']*)'|(y+|M+|d+|H+|h+|m+|s+|S+|a|E+|Z+|z+|X+)|(.)", re.DOTALL)


def _tokens(pattern: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _JAVA_TOKEN.finditer(pattern):
        if m.group(1) is not None:
            out.append(("lit", m.group(1) or "'"))
        elif m.group(2):
            out.append(("field", m.group(2)))
        else:
            out.append(("lit", m.group(3)))
    return out


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def java_format(value: dt.datetime, pattern: str) -> str:
    """Format ``value`` with a Java SimpleDateFormat pattern (common letters only)."""
    out: list[str] = []
    for kind, tok in _tokens(pattern):
        if kind == "lit":
            out.append(tok)
            continue
        c, n = tok[0], len(tok)
        if c == "y":
            out.append(f"{value.year % 100:02d}" if n == 2 else f"{value.year:0{n}d}")
        elif c == "M":
            out.append(_MONTHS[value.month - 1][:3] if n == 3 else
                       _MONTHS[value.month - 1] if n > 3 else f"{value.month:0{n}d}")
        elif c == "d":
            out.append(f"{value.day:0{n}d}")
        elif c == "H":
            out.append(f"{value.hour:0{n}d}")
        elif c == "h":
            out.append(f"{(value.hour % 12) or 12:0{n}d}")
        elif c == "m":
            out.append(f"{value.minute:0{n}d}")
        elif c == "s":
            out.append(f"{value.second:0{n}d}")
        elif c == "S":
            out.append(f"{value.microsecond:06d}"[:n].ljust(n, "0"))
        elif c == "a":
            out.append("AM" if value.hour < 12 else "PM")
        elif c == "E":
            name = _DAYS[value.weekday()]
            out.append(name[:3] if n <= 3 else name)
        elif c in "Zz":
            out.append(value.strftime("%z") or "+0000")
        elif c == "X":
            z = value.strftime("%z")
            out.append("Z" if not z or z == "+0000" else z[:3] + (":" + z[3:] if n > 1 else z[3:]))
    return "".join(out)


def java_parse(pattern: str, text: str) -> dt.datetime:
    """Parse ``text`` with a Java SimpleDateFormat pattern (common letters only)."""
    rx: list[str] = []
    for kind, tok in _tokens(pattern):
        if kind == "lit":
            rx.append(re.escape(tok))
            continue
        c, n = tok[0], len(tok)
        if c == "y":
            rx.append(r"(?P<y>\d{2})" if n == 2 else r"(?P<y>\d{" + str(n) + ",})")
        elif c == "M":
            rx.append(r"(?P<M>[A-Za-z]+)" if n >= 3 else r"(?P<M>\d{1,2})")
        elif c == "d":
            rx.append(r"(?P<d>\d{1,2})")
        elif c == "H":
            rx.append(r"(?P<H>\d{1,2})")
        elif c == "h":
            rx.append(r"(?P<h>\d{1,2})")
        elif c == "m":
            rx.append(r"(?P<m>\d{1,2})")
        elif c == "s":
            rx.append(r"(?P<s>\d{1,2})")
        elif c == "S":
            rx.append(r"(?P<S>\d{1," + str(max(n, 3)) + "})")
        elif c == "a":
            rx.append(r"(?P<a>AM|PM|am|pm)")
        elif c == "E":
            rx.append(r"[A-Za-z]+")
        else:
            rx.append(r"(?P<z>Z|[+-]\d{2}:?\d{2})?")
    m = re.fullmatch("".join(rx), text.strip())
    if not m:
        raise ValueError(f"{text!r} does not match date pattern {pattern!r}")
    g = m.groupdict()
    year = int(g["y"]) if g.get("y") else 1970
    if g.get("y") and len(g["y"]) == 2:
        year += 2000
    month = 1
    if g.get("M"):
        raw = g["M"]
        month = int(raw) if raw.isdigit() else next(
            i + 1 for i, name in enumerate(_MONTHS) if name.lower().startswith(raw.lower()[:3])
        )
    hour = int(g["H"]) if g.get("H") else 0
    if g.get("h"):
        hour = int(g["h"]) % 12 + (12 if (g.get("a") or "").upper() == "PM" else 0)
    micro = int(g["S"].ljust(6, "0")[:6]) if g.get("S") else 0
    return dt.datetime(year, month, int(g.get("d") or 1), hour, int(g.get("m") or 0),
                       int(g.get("s") or 0), micro)


# --- atomisation helpers --------------------------------------------------------


def _first(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _atomic(value: Any) -> Any:
    """Atomise one argument handed to an external function (node, list of nodes, scalar)."""
    value = _first(value)
    if value is None:
        return None
    string_value = getattr(value, "string_value", None)
    if string_value is not None:
        return string_value
    if isinstance(value, etree._Element):
        return "".join(value.itertext())
    return value


def _as_datetime(value: Any) -> dt.datetime:
    value = _atomic(value)
    if isinstance(value, dt.datetime):
        return value
    py = getattr(value, "_dt", None)
    if isinstance(py, dt.datetime):
        return py
    if hasattr(value, "year") and hasattr(value, "hour"):
        return dt.datetime(value.year, value.month, value.day, value.hour, value.minute,
                           int(value.second), getattr(value, "microsecond", 0))
    return dt.datetime.fromisoformat(str(value))


def _to_xs_datetime(value: dt.datetime) -> DateTime10:
    return DateTime10.fromdatetime(value)


def _plain(value: Any) -> Any:
    """Atomise an XPath result item for callers: nodes become their string value."""
    return _atomic(value) if not isinstance(value, (bool, int, float, str)) else value


def _node_xml(value: Any) -> str:
    value = _first(value)
    elem = getattr(value, "elem", value)
    if isinstance(elem, etree._Element):
        return etree.tostring(elem, encoding="unicode", with_tail=False)
    return str(_atomic(value) if value is not None else "")


# --- context --------------------------------------------------------------------


class XPathContext:
    """Variables plus module properties, evaluated with XPath 2.0 and BW's custom functions.

    ``variables`` maps a BW variable name (``Start``, ``ComputeDTI``, ...) to an lxml element
    (or a plain scalar). ``substvars`` are the module/global properties read by
    ``bw:getModuleProperty``. ``namespaces`` adds prefixes used by paths such as
    ``$X/tns:Payment``. ``now`` freezes ``current-dateTime()`` and ``tib:`` clock helpers.
    """

    def __init__(
        self,
        variables: dict[str, Any],
        substvars: dict[str, str],
        namespaces: dict[str, str] | None = None,
        now: str | dt.datetime | None = None,
    ) -> None:
        self.variables = dict(variables)
        self.substvars = dict(substvars)
        self.namespaces = {**DEFAULT_NAMESPACES, **(namespaces or {})}
        if isinstance(now, str):
            now = dt.datetime.fromisoformat(now)
        self.now = now
        self._parser = self._build_parser()

    def bind(self, name: str, value: Any) -> None:
        """Add or replace a variable (after an activity has produced its output)."""
        self.variables[name] = value

    def _clock(self) -> dt.datetime:
        return self.now or dt.datetime.now().replace(microsecond=0)

    def _build_parser(self) -> XPath2Parser:
        parser = XPath2Parser(namespaces=self.namespaces)
        ns = self.namespaces
        if "bw" in ns and "tib" in ns:
            self._register(parser)
        return parser

    def _register(self, parser: XPath2Parser) -> None:
        substvars = self.substvars

        def get_module_property(name):
            key = str(_atomic(name))
            if key not in substvars:
                raise KeyError(f"module property {key!r} is not defined")
            return substvars[key]

        def trim(value):
            atom = _atomic(value)
            return "" if atom is None else str(atom).strip()

        def render_xml(node):
            return _node_xml(node)

        def parse_datetime(pattern, text):
            return _to_xs_datetime(java_parse(str(_atomic(pattern)), str(_atomic(text))))

        def format_datetime(pattern, value):
            return java_format(_as_datetime(value), str(_atomic(pattern)))

        def current_datetime():
            return _to_xs_datetime(self._clock())

        def timestamp():
            return self._clock().isoformat()

        reg: list[tuple[Callable[..., Any], str, str, tuple[str, ...]]] = [
            (get_module_property, "getModuleProperty", "bw", ("item()*", "xs:string")),
            (trim, "trim", "tib", ("item()*", "xs:string")),
            (render_xml, "render-xml", "tib", ("item()*", "xs:string")),
            (parse_datetime, "parse-dateTime", "tib", ("item()*", "item()*", "xs:dateTime")),
            (format_datetime, "format-dateTime", "tib", ("item()*", "item()*", "xs:string")),
            (current_datetime, "current-dateTime", "tib", ("xs:dateTime",)),
            (timestamp, "timestamp", "tib", ("xs:string",)),
        ]
        for fn, name, prefix, types in reg:
            parser.external_function(fn, name=name, prefix=prefix, sequence_types=types)

    def _ep_context(self) -> _EPContext:
        return _EPContext(
            etree.Element("context"),
            variables=self.variables,
            current_dt=self._clock() if self.now else None,
        )

    def evaluate_items(self, expr: str) -> Any:
        """Raw ``elementpath`` result: a list of items for path expressions, else a scalar."""
        return self._parser.parse(expr).get_results(self._ep_context())

    def evaluate(self, expr: str) -> Any:
        """Evaluate and atomise: empty sequence -> ``None``, one item -> scalar, else a list.

        Nodes are returned as their string value (what ``xsl:value-of`` would emit); use
        :meth:`evaluate_items` when the nodes themselves are needed.
        """
        result = self.evaluate_items(expr)
        if isinstance(result, list):
            atoms = [_plain(item) for item in result]
            if not atoms:
                return None
            return atoms[0] if len(atoms) == 1 else atoms
        return _plain(result)
