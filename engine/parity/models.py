"""Scenario, result and report models of the parity evaluator (design spec sections 6.3, 6.4).

Also the two dict/XML boundary helpers: scenario inputs arrive as dicts and become XML
elements for the XPath/XSLT engines; results leave the engines as XML and become dicts again.
"""
from __future__ import annotations

from typing import Any, Literal

from lxml import etree
from pydantic import BaseModel, Field


class StubResponse(BaseModel):
    status: int
    body: dict | None = None
    delay_ms: int = 0


class SeedRow(BaseModel):
    table: str
    row: dict[str, Any]


class SideEffect(BaseModel):
    kind: Literal["row_written", "message_published", "service_called", "subprocess_called"]
    target: str
    payload: dict[str, Any] = Field(default_factory=dict)
    # Provenance (trace path of the activity that caused it); not compared, used for lineage.
    activity_id: str | None = None


class Fault(BaseModel):
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class TraceStep(BaseModel):
    activity_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    chosen_transition: str | None = None
    # Binding result fields computed in XPath double arithmetic (``number(a) div number(b)``),
    # by local path ("dti", "approval/rate"): value "double".
    numeric_kinds: dict[str, str] = Field(default_factory=dict)


class ExpectedResult(BaseModel):
    output: dict[str, Any] = Field(default_factory=dict)
    side_effects: list[SideEffect] = Field(default_factory=list)
    fault: Fault | None = None
    trace: list[TraceStep] = Field(default_factory=list)
    source: Literal["model", "sandbox"] = "model"
    # Comparison paths of double-derived fields -> decimal scale to compare at (the contract's
    # fractionDigits or the column scale); None compares at 15 significant digits.
    numeric_fields: dict[str, int | None] = Field(default_factory=dict)


class Scenario(BaseModel):
    id: str
    process_id: str
    name: str
    kind: Literal["happy", "branch", "fault", "external_failure", "sme"]
    input: dict[str, Any]
    stub_responses: dict[str, StubResponse] = Field(default_factory=dict)
    seed_state: list[SeedRow] = Field(default_factory=list)
    expected: ExpectedResult | None = None
    source: Literal["generated", "sme"] = "generated"


class FieldDiff(BaseModel):
    path: str
    expected: Any = None
    actual: Any = None
    status: Literal["match", "defect", "intended", "noise"]
    finding_id: str | None = None


class ParityReport(BaseModel):
    scenario_id: str
    fields: list[FieldDiff] = Field(default_factory=list)
    side_effects: list[FieldDiff] = Field(default_factory=list)
    parity: float
    unexplained: int


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _fill(el: etree._Element, value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, list):
                for item in child:
                    _fill(etree.SubElement(el, key), item)
            else:
                _fill(etree.SubElement(el, key), child)
    elif value is not None:
        el.text = _scalar_text(value)


def dict_to_xml(d: dict, root: str) -> etree._Element:
    """Build an unqualified element tree: keys become children, lists repeat the child.

    Booleans render as ``true``/``false`` (xs:boolean lexical form); ``None`` yields an empty
    element. This is the scenario-input boundary: BW variables are XML, scenarios are dicts.
    """
    el = etree.Element(root)
    _fill(el, d)
    return el


def xml_to_dict(el: etree._Element, lists: set[str] | frozenset[str] = frozenset()) -> dict:
    """Inverse of :func:`dict_to_xml` for the output boundary.

    Namespaces and prefixes are dropped; leaves become strings (XML carries no types, the
    comparison layer normalises numbers). An element name that repeats among siblings becomes
    a list; names in ``lists`` become a list even when they occur once, which is how callers
    keep a repeating element stable across one-row and many-row results.
    """
    return _children_to_dict(el, set(lists))


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _node_value(el: etree._Element, lists: set[str]) -> Any:
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids:
        return (el.text or "").strip() if len(el.attrib) == 0 else _with_attrs(el, {}, lists)
    return _children_to_dict(el, lists)


def _with_attrs(el: etree._Element, base: dict, lists: set[str]) -> dict:
    out = {f"@{etree.QName(k).localname}": v for k, v in el.attrib.items()}
    out.update(base)
    text = (el.text or "").strip()
    if text:
        out["#text"] = text
    return out


def _children_to_dict(el: etree._Element, lists: set[str]) -> dict:
    result: dict[str, Any] = {}
    kids = [c for c in el if isinstance(c.tag, str)]
    names = [_local(c) for c in kids]
    for child in kids:
        name = _local(child)
        value = _node_value(child, lists)
        if names.count(name) > 1 or name in lists:
            result.setdefault(name, []).append(value)
        else:
            result[name] = value
    if el.attrib:
        result = _with_attrs(el, result, lists)
    return result
