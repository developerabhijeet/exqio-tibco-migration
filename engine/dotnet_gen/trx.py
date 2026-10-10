"""TRX (VSTest results) parsing and the test naming convention (design spec 2.5).

The generated tests follow one naming convention so a result maps back to what it proves:

==================  ==========================================  ===========================
test class          display name                                id taken from
==================  ==========================================  ===========================
``*RulesTests``     ``R1 <rule text>``                          first token: ``rule_id``
``*BranchTests``    ``Branch [<transition id>] <from> to ...``  text in brackets: ``branch_id``
``*ScenarioTests``  ``Scenario [<scenario id>] <name>``         text in brackets: ``scenario_id``
==================  ==========================================  ===========================
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from lxml import etree
from pydantic import BaseModel

NS = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
Outcome = Literal["passed", "failed", "skipped"]

RULES_SUFFIX = "RulesTests"
BRANCH_SUFFIX = "BranchTests"
SCENARIO_SUFFIX = "ScenarioTests"
BRANCH_PREFIX = "Branch"
SCENARIO_PREFIX = "Scenario"

_FAILED = {"failed", "error", "timeout", "aborted"}
_PASSED = {"passed", "passedbutrunaborted"}
_BRACKET = re.compile(r"\[([^\]]+)\]")
_DURATION = re.compile(r"^(?:(\d+)\.)?(\d+):(\d+):(\d+(?:\.\d+)?)$")


class TestCase(BaseModel):
    __test__ = False  # not a pytest test class

    name: str  # fully qualified method: <class>.<method>
    display_name: str
    outcome: Outcome
    message: str = ""
    rule_id: str | None = None
    branch_id: str | None = None
    scenario_id: str | None = None
    duration_ms: float = 0.0


def classify(class_name: str, method_name: str, display_name: str) -> tuple[str | None, str | None, str | None]:
    """``(rule_id, branch_id, scenario_id)`` from the naming convention; ``None`` when not one of ours."""
    cls = class_name.rsplit(".", 1)[-1]
    display = display_name.strip()
    if cls.endswith(RULES_SUFFIX):
        if display and display != method_name and not display.endswith(f".{method_name}"):
            return display.split()[0], None, None
        return (method_name.split("_", 1)[0] or None), None, None
    m = _BRACKET.search(display)
    if cls.endswith(BRANCH_SUFFIX) and m:
        return None, m.group(1), None
    if cls.endswith(SCENARIO_SUFFIX) and m:
        return None, None, m.group(1)
    return None, None, None


def _duration_ms(text: str | None) -> float:
    m = _DURATION.match((text or "").strip())
    if not m:
        return 0.0
    days, hours, minutes, seconds = m.groups()
    total = (int(days or 0) * 86400) + int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return round(total * 1000.0, 6)


def _outcome(text: str | None) -> Outcome:
    value = (text or "").strip().lower()
    if value in _PASSED:
        return "passed"
    if value in _FAILED:
        return "failed"
    return "skipped"


def parse_trx(path: str | Path) -> list[TestCase]:
    """Every ``UnitTestResult`` in a TRX file as a :class:`TestCase`, in file order."""
    root = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
    methods: dict[str, tuple[str, str]] = {}
    for ut in root.iterfind("t:TestDefinitions/t:UnitTest", NS):
        tm = ut.find("t:TestMethod", NS)
        if tm is not None:
            methods[ut.get("id", "")] = (tm.get("className", ""), tm.get("name", ""))
    cases: list[TestCase] = []
    for res in root.iterfind("t:Results/t:UnitTestResult", NS):
        display = res.get("testName", "")
        class_name, method = methods.get(res.get("testId", ""), ("", display))
        msg_el = res.find("t:Output/t:ErrorInfo/t:Message", NS)
        message = (msg_el.text or "").strip() if msg_el is not None else ""
        rule_id, branch_id, scenario_id = classify(class_name, method, display)
        cases.append(TestCase(
            name=f"{class_name}.{method}" if class_name else method,
            display_name=display,
            outcome=_outcome(res.get("outcome")),
            message=message,
            rule_id=rule_id,
            branch_id=branch_id,
            scenario_id=scenario_id,
            duration_ms=_duration_ms(res.get("duration")),
        ))
    return cases
