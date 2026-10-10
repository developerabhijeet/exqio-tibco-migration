"""XSLT input-binding execution (design spec section 6.2).

BW6 input bindings are XSLT stylesheets whose top-level ``xsl:param`` names are the process
variables they read (``$Start``, ``$ComputeDTI``, ...). ``transform`` binds each declared
param from an :class:`XPathContext` and returns the result element.

* Primary engine: SaxonC-HE (``saxonche``), which runs XSLT 2.0/3.0 as BW's engine does. A
  variable is bound as the *element* node (so ``$Start/parameters/x`` works); a declared param
  with no variable bound (an activity on a branch that did not run) is the empty sequence.
* ``tib:trim`` and ``tib:render-xml`` (used inside real bindings) are bridged: Saxon has no
  extension-function registration in ``saxonche``, so the calls are rewritten to equivalent
  XPath 2.0 before compiling (``tib:trim(x)`` -> ``replace(replace(string(x), '^\\s+', ''),
  '\\s+$', '')``; ``tib:render-xml(x)`` -> ``serialize(x)``). The lxml fallback registers them as
  native lxml extension functions instead.
* BW5 bindings are bare ``pd:inputBindings`` fragments, not stylesheets; use
  :func:`bw5_binding_stylesheet` to wrap one before calling :func:`transform`.
* Fallback: lxml/libxslt (XSLT 1.0), used only when ``saxonche`` cannot be imported. It emits
  ``warnings.warn("xslt downgraded to 1.0")``. libxslt only accepts string parameters, so the
  fallback rewrites each declared ``xsl:param`` into a global ``xsl:variable`` that selects
  from a synthetic ``<_vars>`` source document holding the bound variables (scalars become
  strings). XSLT 2.0-only functions fail under the fallback.
"""
from __future__ import annotations

import copy
import re
import warnings
from typing import Any

from lxml import etree

from engine.parity.xpath import TIB_NS, XPathContext

XSL_NS = "http://www.w3.org/1999/XSL/Transform"
_XSL = f"{{{XSL_NS}}}"

_saxon_processor: Any = None


class XsltError(ValueError):
    """The stylesheet failed to compile/run or produced no element."""


def _saxon_available() -> bool:
    try:
        import saxonche  # noqa: F401
    except ImportError:
        return False
    return True


def _processor() -> Any:
    global _saxon_processor
    if _saxon_processor is None:
        from saxonche import PySaxonProcessor

        _saxon_processor = PySaxonProcessor(license=False)
    return _saxon_processor


# --- tib: function bridge -------------------------------------------------------

_TIB_REWRITES = {
    "trim": lambda arg: f"replace(replace(string({arg}), '^\\s+', ''), '\\s+$', '')",
    "render-xml": lambda arg: f"serialize({arg})",
}


def _tib_prefixes(sheet: etree._Element) -> list[str]:
    prefixes = {"tib"}
    for el in sheet.iter():
        if isinstance(el.tag, str):
            prefixes.update(p for p, uri in el.nsmap.items() if p and uri == TIB_NS)
    return sorted(prefixes)


def _split_args(text: str) -> list[str]:
    args, depth, quote, start = [], 0, "", 0
    for i, c in enumerate(text):
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            args.append(text[start:i])
            start = i + 1
    args.append(text[start:])
    return args


def rewrite_tib_calls(expr: str, prefixes: list[str] | tuple[str, ...] = ("tib",)) -> str:
    """Rewrite ``tib:trim``/``tib:render-xml`` calls in an XPath text to plain XPath 2.0."""
    names = "|".join(re.escape(p) for p in prefixes)
    pattern = re.compile(rf"(?<![\w.:-])(?:{names}):(trim|render-xml)\s*\(")
    out, pos = [], 0
    while True:
        m = pattern.search(expr, pos)
        if not m:
            out.append(expr[pos:])
            return "".join(out)
        depth, quote, i = 1, "", m.end()
        while i < len(expr) and depth:
            c = expr[i]
            if quote:
                quote = "" if c == quote else quote
            elif c in "'\"":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        inner = expr[m.end():i - 1]
        first = rewrite_tib_calls(_split_args(inner)[0], prefixes)
        out.append(expr[pos:m.start()])
        out.append(_TIB_REWRITES[m.group(1)](first))
        pos = i


def _rewrite_stylesheet_tib(sheet: etree._Element) -> None:
    prefixes = _tib_prefixes(sheet)
    for el in sheet.iter():
        if not isinstance(el.tag, str):
            continue
        for key, value in list(el.attrib.items()):
            if ":" in value and any(f"{p}:" in value for p in prefixes):
                el.set(key, rewrite_tib_calls(value, prefixes))


def _lxml_text(arg: Any) -> str:
    if isinstance(arg, list):
        arg = arg[0] if arg else ""
    if isinstance(arg, etree._Element):
        return "".join(arg.itertext())
    return str(arg)


def _lxml_extensions(sheet: etree._Element) -> dict[tuple[str, str], Any]:
    def trim(_ctx: Any, *args: Any) -> str:
        return _lxml_text(args[0]).strip()

    def render_xml(_ctx: Any, *args: Any) -> str:
        arg = args[0]
        node = arg[0] if isinstance(arg, list) and arg else arg
        if isinstance(node, etree._Element):
            return etree.tostring(node, encoding="unicode", with_tail=False)
        return _lxml_text(arg)

    uris = {TIB_NS}
    for el in sheet.iter():
        if isinstance(el.tag, str) and el.nsmap.get("tib"):
            uris.add(el.nsmap["tib"])
    return {(uri, name): fn for uri in uris
            for name, fn in (("trim", trim), ("render-xml", render_xml))}


def bw5_binding_stylesheet(bindings: etree._Element) -> str:
    """Wrap a BW5 ``pd:inputBindings`` fragment into a standalone XSLT 2.0 stylesheet.

    Every ``$name`` referenced in the fragment becomes a top-level ``xsl:param`` (BW5 variable
    names such as ``HTTP-Receiver`` are legal QNames). The fragment's in-scope namespace
    declarations are kept so ``pfx:`` and ``tib:`` resolve.
    """
    refs: set[str] = set()
    for el in bindings.iter():
        if isinstance(el.tag, str):
            for value in el.attrib.values():
                refs.update(n.rstrip("-.") for n in re.findall(r"\$([A-Za-z_][\w.-]*)", value))
    nsmap = dict(bindings.nsmap)
    nsmap["xsl"] = XSL_NS
    sheet = etree.Element(f"{_XSL}stylesheet", nsmap=nsmap, version="2.0")
    sheet.set("exclude-result-prefixes", "#all")
    for name in sorted(refs):
        etree.SubElement(sheet, f"{_XSL}param", name=name)
    template = etree.SubElement(sheet, f"{_XSL}template", match="/")
    for child in bindings:
        template.append(copy.deepcopy(child))
    return etree.tostring(sheet, encoding="unicode")


def _parse_stylesheet(stylesheet: str | bytes) -> etree._Element:
    data = stylesheet.encode("utf-8") if isinstance(stylesheet, str) else stylesheet
    return etree.fromstring(data.lstrip())


def _top_level_params(sheet: etree._Element) -> list[etree._Element]:
    return [p for p in sheet.findall(f"{_XSL}param")]


def _as_element(value: Any) -> etree._Element | None:
    if isinstance(value, etree._ElementTree):
        return value.getroot()
    if isinstance(value, etree._Element):
        return value
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], etree._Element):
        return value[0]
    return None


def _empty_param_to_empty_sequence(sheet: etree._Element, names: set[str], select: str) -> None:
    for param in _top_level_params(sheet):
        if param.get("name") in names and param.get("select") is None and len(param) == 0:
            param.set("select", select)


def transform(stylesheet: str | bytes, context: XPathContext) -> etree._Element:
    """Run an input-binding stylesheet against the variables in ``context``.

    Returns the root element of the result tree (e.g. ``tns:TierAssignment``).
    """
    sheet = _parse_stylesheet(stylesheet)
    if _saxon_available():
        out = _run_saxon(sheet, context)
    else:
        warnings.warn("xslt downgraded to 1.0", UserWarning, stacklevel=2)
        out = _run_lxml(sheet, context)
    try:
        result = etree.fromstring(out)
    except etree.XMLSyntaxError as exc:
        raise XsltError(f"binding produced no XML element: {out[:80]!r}") from exc
    return result


def _run_saxon(sheet: etree._Element, context: XPathContext) -> bytes:
    from saxonche import PySaxonApiError

    proc = _processor()
    _rewrite_stylesheet_tib(sheet)
    declared = {p.get("name") for p in _top_level_params(sheet)}
    unbound = {n for n in declared if n not in context.variables}
    _empty_param_to_empty_sequence(sheet, unbound, "()")
    xslt = proc.new_xslt30_processor()
    for name in declared - unbound:
        value = context.variables[name]
        el = _as_element(value)
        if el is not None:
            doc = proc.parse_xml(xml_text=etree.tostring(el, encoding="unicode"))
            xslt.set_parameter(name, doc.children[0])
        elif isinstance(value, bool):
            xslt.set_parameter(name, proc.make_boolean_value(value))
        elif isinstance(value, int):
            xslt.set_parameter(name, proc.make_integer_value(value))
        elif isinstance(value, float):
            xslt.set_parameter(name, proc.make_double_value(value))
        else:
            xslt.set_parameter(name, proc.make_string_value(str(value)))
    try:
        executable = xslt.compile_stylesheet(stylesheet_text=etree.tostring(sheet, encoding="unicode"))
        text = executable.transform_to_string(xdm_node=proc.parse_xml(xml_text="<_/>"))
    except PySaxonApiError as exc:
        raise XsltError(str(exc)) from exc
    if text is None:
        raise XsltError("stylesheet produced no output")
    return text.encode("utf-8")


def _run_lxml(sheet: etree._Element, context: XPathContext) -> bytes:
    names = {p.get("name") for p in _top_level_params(sheet)}
    source = etree.Element("_vars")
    for name in names:
        if name not in context.variables:
            continue
        holder = etree.SubElement(source, name)
        el = _as_element(context.variables[name])
        if el is not None:
            holder.append(etree.fromstring(etree.tostring(el)))
        else:
            holder.text = str(context.variables[name])
    for param in _top_level_params(sheet):
        name = param.get("name")
        was_element = _as_element(context.variables.get(name)) is not None
        var = etree.Element(f"{_XSL}variable", name=name)
        var.set("select", f"/_vars/{name}/*[1]" if was_element or name not in context.variables
                else f"string(/_vars/{name})")
        sheet.replace(param, var)
    try:
        result = etree.XSLT(sheet, extensions=_lxml_extensions(sheet))(source)
    except (etree.XSLTError, etree.XPathError) as exc:
        raise XsltError(str(exc)) from exc
    return etree.tostring(result)
