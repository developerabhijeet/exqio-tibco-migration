"""The design is stack-aware (target-stacks spec section 2) and contracts go through a neutral model.

Golden files (``engine/tests/golden/``):

* ``design_record_v1.json``: a .NET design record as stored before ``DesignRecord.stack`` existed.
  It is frozen: never regenerated, because it pins that an upgrade keeps every stored hash.
* ``design_skeleton_dfr.json`` and ``contracts_gmf_credit.txt``: today's .NET skeleton for
  DealerFundingRequest and every gmf.credit contract rendered as C#. Regenerate them with
  ``UPDATE_GOLDEN=1`` only after an intended .NET change, and review the diff.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from engine.domains import graph as graph_mod
from engine.dotnet_gen import csharp_contracts
from engine.dotnet_gen.design import build_design_skeleton
from engine.dotnet_gen.models import DesignRecord, compute_design_hash
from engine.dotnet_gen.patterns import PATTERNS
from engine.stacks import get_stack
from engine.stacks.base import StackNaming
from engine.tests.conftest import DFR, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def _golden(name: str, actual: str, frozen: bool = False) -> str:
    path = GOLDEN / name
    if (UPDATE and not frozen) or not path.exists():
        path.write_text(actual, encoding="utf-8")
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rules(gmf_rules):
    return gmf_rules


@pytest.fixture(scope="module")
def findings(gmf_findings):
    return gmf_findings


@pytest.fixture(scope="module")
def designs(gmf_designs):
    return gmf_designs


@pytest.fixture(scope="module")
def cards(gmf_apps, gmf_app, gmf_processes):
    graph = graph_mod.build_graph(list(gmf_apps.values()))
    return {pid: graph_mod.io_card(gmf_processes[pid], gmf_app, graph) for pid in PROCESS_IDS}


def render_all_records(app) -> str:
    """Every contract of ``app`` rendered as C#, in plan order, as the generator writes them."""
    app_name = csharp_contracts.pascal(app.id)
    plans = csharp_contracts.plan_contracts(app_name, app.contracts)
    by_id = {c.id: c for c in app.contracts}
    parts = []
    for cid in sorted(plans):
        plan = plans[cid]
        parts.append(f"==== {cid} -> {plan.file} ({plan.namespace})\n")
        parts.append(csharp_contracts.to_records(by_id[cid], plan.namespace, plan.reserved))
    return "".join(parts)


def _stored_record(designs) -> str:
    """A stored record as the design job wrote it before this change: the skeleton with the
    designer's narrative (``record=design.model_dump(mode="json", by_alias=True)``)."""
    rec = designs[DFR].model_copy(update={"narrative": "Stored before target stacks."})
    return json.dumps(rec.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True) + "\n"


# ------------------------------------------------------------------- design hash
def test_stored_dotnet_record_hash_is_unchanged(designs):
    stored = json.loads(_golden("design_record_v1.json", _stored_record(designs), frozen=True))
    assert "stack" not in stored  # captured before the field existed
    rec = DesignRecord.model_validate(stored)
    assert rec.stack == "dotnet" and compute_design_hash(rec) == stored["design_hash"]


def test_stack_moves_the_hash_for_non_dotnet(designs):
    d = designs[DFR]
    assert compute_design_hash(d.model_copy(update={"stack": "java"})) != compute_design_hash(d)
    assert compute_design_hash(d.model_copy(update={"stack": "dotnet"})) == compute_design_hash(d)


# ------------------------------------------------------------------------ naming
def test_dotnet_naming_reproduces_every_pattern():
    naming = get_stack("dotnet").naming
    assert isinstance(naming, StackNaming)
    for name, spec in PATTERNS.items():
        assert naming.pattern_paths(name) == (spec.file_template, spec.class_template, spec.method_template)
    with pytest.raises(KeyError):
        naming.pattern_paths("no.such.pattern")


def test_dotnet_naming_members():
    naming = get_stack("dotnet").naming
    assert naming.type_name("dealer funding-request") == "DealerFundingRequest"
    assert naming.type_name("9lives") == "Value9lives"
    assert naming.method_name("", "Lookup Dealer") == "LookupDealerAsync"
    assert naming.method_name("Execute", "") == "ExecuteAsync"
    assert naming.method_name("Handle", "funding event") == "HandleFundingEventAsync"
    assert [naming.layer_folder(k, "GmfCredit") for k in ("api", "core", "infrastructure", "tests")] == [
        "GmfCredit.Api", "GmfCredit.Core", "GmfCredit.Infrastructure", "GmfCredit.Tests"]
    assert naming.contract_target("GmfCredit", "DealerFunding", "funding.request") == (
        "GmfCredit.Core.Contracts.DealerFunding", "Contracts/DealerFunding/FundingRequest.cs")
    assert naming.contract_target("GmfCredit", "", "Funding") == ("GmfCredit.Core.Contracts", "Contracts/Funding.cs")
    assert naming.config_key("DealerFunding", "DbUrl") == "Gmf:DealerFunding:DbUrl"


# ---------------------------------------------------------------------- skeleton
def test_dotnet_skeleton_is_unchanged(gmf_app, gmf_processes, rules, findings, cards):
    after = build_design_skeleton(gmf_processes[DFR], gmf_app, rules[DFR], findings[DFR], cards[DFR])
    dumped = after.model_dump(mode="json", by_alias=True)
    dumped.pop("stack", None)  # the golden was captured before the field; its value is pinned below
    before = json.loads(_golden("design_skeleton_dfr.json", json.dumps(dumped, indent=2, sort_keys=True) + "\n"))
    assert after.stack == "dotnet"
    assert dumped == before


def test_skeleton_takes_the_stack(gmf_app, gmf_processes, rules, findings, cards):
    rec = build_design_skeleton(gmf_processes[DFR], gmf_app, rules[DFR], findings[DFR], cards[DFR],
                                stack="dotnet")
    assert rec.stack == "dotnet"
    with pytest.raises(KeyError, match="not implemented yet"):
        build_design_skeleton(gmf_processes[DFR], gmf_app, rules[DFR], findings[DFR], cards[DFR], stack="java")


# --------------------------------------------------------------------- contracts
def test_csharp_contracts_byte_identical_through_neutral_model(gmf_app):
    actual = render_all_records(gmf_app)
    assert actual == _golden("contracts_gmf_credit.txt", actual)


def test_neutral_contract_model_has_no_csharp_types(gmf_app):
    from engine.stacks.contracts import SCALARS, contract_model

    seen: set[str] = set()
    for c in gmf_app.contracts:
        model = contract_model(c)
        for rec in model.records.values():
            assert rec.tibco_path.startswith("/")
            for p in rec.props:
                assert p.type_ref in SCALARS or p.type_ref in model.records or p.type_ref in model.enums
                assert p.tibco_path.startswith(rec.tibco_path)
                assert p.enum_values == (model.enums[p.type_ref].values if p.type_ref in model.enums else [])
                seen.add(p.type_ref)
    assert not seen & {"DateOnly", "DateTimeOffset", "TimeOnly", "byte[]", "object"}
    assert {"string", "decimal"} <= seen


def test_neutral_model_types_and_lineage():
    from engine.ir.models import Contract
    from engine.stacks.contracts import contract_model

    xsd = """<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="Order">
    <xs:complexType><xs:sequence>
      <xs:element name="placed" type="xs:dateTime"/>
      <xs:element name="due" type="xs:date" minOccurs="0"/>
      <xs:element name="line" maxOccurs="unbounded"><xs:complexType>
        <xs:attribute name="sku" type="xs:string" use="required"/>
      </xs:complexType></xs:element>
      <xs:element name="state"><xs:simpleType><xs:restriction base="xs:string">
        <xs:enumeration value="OPEN"/><xs:enumeration value="SHIPPED"/>
      </xs:restriction></xs:simpleType></xs:element>
    </xs:sequence></xs:complexType>
  </xs:element>
</xs:schema>"""
    model = contract_model(Contract(id="Shop/Schemas/Order.xsd", kind="xsd", path="Order.xsd",
                                    root_elements=["Order"], text=xsd))
    order = model.records["Order"]
    assert [(p.name, p.json_name, p.type_ref, p.required, p.repeated, p.tibco_path) for p in order.props] == [
        ("Placed", "placed", "datetime", True, False, "/Order/placed"),
        ("Due", "due", "date", False, False, "/Order/due"),
        ("Line", "line", "Line", True, True, "/Order/line"),
        ("State", "state", "State", True, False, "/Order/state"),
    ]
    assert order.props[3].enum_values == ["OPEN", "SHIPPED"]
    assert model.records["Line"].props[0].tibco_path == "/Order/line/@sku"
    assert model.enums["State"].values == ["OPEN", "SHIPPED"]


def test_plan_contracts_takes_the_stack_naming(gmf_app):
    from engine.stacks.contracts import plan_contracts

    naming = get_stack("dotnet").naming
    neutral = plan_contracts("GmfCredit", gmf_app.contracts, naming)
    assert neutral == csharp_contracts.plan_contracts("GmfCredit", gmf_app.contracts)

    class Upper:  # a stand-in stack: its contract_target decides namespace and file
        def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]:
            return f"{app.lower()}.{module.lower()}", f"contracts/{module.lower()}/{stem.lower()}.x"

    other = plan_contracts("GmfCredit", gmf_app.contracts, Upper())  # type: ignore[arg-type]
    assert set(other) == set(neutral)
    for cid, plan in other.items():
        assert plan.namespace.startswith("gmfcredit.") and plan.file.endswith(".x")
        assert plan.record_names == neutral[cid].record_names
    assert len({p.file for p in other.values()}) == len(other)
