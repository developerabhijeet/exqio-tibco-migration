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
from engine.stacks import all_stacks, get_stack
from engine.stacks.base import StackNaming
from engine.tests.conftest import DFR, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def _golden(name: str, actual: str, frozen: bool = False) -> str:
    """The golden file's text; written only with ``UPDATE_GOLDEN=1`` (never for a frozen one), so a
    missing golden fails instead of passing against itself."""
    path = GOLDEN / name
    if UPDATE and not frozen:
        path.write_text(actual, encoding="utf-8")
    if not path.exists():
        pytest.fail(f"golden file {name} is missing; write it with UPDATE_GOLDEN=1 and review it")
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
    words = {"app": "gmf.credit", "process": "DealerFundingRequest", "activity": "Lookup Dealer",
             "starter": "get", "resource": "EquifaxClient", "fault": "DealerInactiveFault", "target": "",
             "destination": "gmf.funding.events"}
    assert naming.row_names("jdbc.query", words) == (
        "Repositories/DealerFundingRequestRepository.cs", "DealerFundingRequestRepository", "LookupDealerAsync")
    assert naming.row_names("http.invoke", words)[1] == "EquifaxClient"  # Equifax + Client, not EquifaxClientClient
    assert naming.row_names("throw", words)[1] == "DealerInactiveFaultException"


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
    # raw words in, cased here
    assert naming.config_key("dealerfunding", "GMF.DB.URL") == "Gmf:Dealerfunding:GmfDbUrl"
    assert naming.config_key("dealerfunding", "GMF FundingDB", "password") == "Gmf:Dealerfunding:GmfFundingDB:Password"
    assert naming.connection_key("GMF FundingDB") == "ConnectionStrings:GmfFundingDB"
    assert naming.exception_name("DealerInactiveFault") == "DealerInactiveFaultException"
    assert naming.exception_name("Timeout Exception") == "TimeoutException"
    assert naming.file_name("lookup dealer") == "LookupDealer.cs"
    assert naming.escape("class") == "@class" and naming.escape("Class") == "Class"
    assert (naming.platform, naming.config_file, naming.messaging_actor) == (".NET", "appsettings.json", "ServiceBus")
    scaffold = naming.layout_scaffold({"api": "A.Api", "core": "A.Core", "infrastructure": "A.Infrastructure",
                                       "tests": "A.Tests"}, "Proc", {"jms.receive", "throw"})
    assert "A.Api.csproj" in scaffold["api"] and "Errors/FaultExceptionHandler.cs" in scaffold["api"]
    assert "Messaging/IMessageBus.cs" in scaffold["core"] and "TestDatabase.cs" not in scaffold["tests"]


class SnakeNaming:
    """A stand-in stack with Python-like conventions: the contract without any .NET in it."""

    platform = "Snake"
    config_file = "settings.toml"
    messaging_actor = "Broker"
    reserved = frozenset({"class", "def"})

    @staticmethod
    def _words(text: str) -> list[str]:
        import re
        return [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", text)]

    def escape(self, name: str) -> str:
        return f"{name}_" if name in self.reserved else name

    def type_name(self, words: str, fallback: str = "Value") -> str:
        out = "".join(w.capitalize() for w in self._words(words))
        if not out:
            return fallback
        return f"{fallback}{out}" if out[0].isdigit() else out

    def method_name(self, prefix: str, words: str) -> str:
        name = "_".join(self._words(prefix) + self._words(words)) or "execute"
        return self.escape(f"step_{name}" if name[0].isdigit() else name)

    def file_name(self, words: str) -> str:
        return "_".join(self._words(words)) + ".snk"

    def exception_name(self, words: str) -> str:
        return self.type_name(words, "Step") + "Error"

    def config_key(self, module: str, *names: str) -> str:
        return "_".join(["GMF", *("_".join(self._words(n)).upper() for n in (module, *names))])

    def connection_key(self, resource: str) -> str:
        return "DB_" + "_".join(self._words(resource)).upper()

    def row_names(self, pattern: str, words: dict[str, str]) -> tuple[str, str, str]:
        PATTERNS[pattern]  # KeyError for an unknown pattern, as the contract says
        folder = pattern.replace(".", "_")
        return (f"{folder}/{self.file_name(words['process'])}", self.type_name(f"{words['process']} {folder}"),
                self.method_name("run", words["activity"]))

    def layer_folder(self, kind: str, app: str) -> str:
        return f"{app.lower()}/{kind}"

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]:
        return f"{app.lower()}.contracts", f"contracts/{self.file_name(stem)}"

    def layout_scaffold(self, projects: dict[str, str], process: str,
                        patterns: set[str]) -> dict[str, set[str]]:
        return {"core": {f"{process}.main.snk"}}


def _naming_contract(naming) -> None:
    """What every stack's naming must do, whatever its casing (``StackNaming``)."""
    assert isinstance(naming, StackNaming)
    for words in ("Lookup Dealer", "LookupDealer", "lookup-dealer", "GMF.DB.URL", "9lives", "class"):
        t = naming.type_name(words)
        assert t and t.isidentifier() and not t[0].isdigit() and t not in naming.reserved
        m = naming.method_name("", words)
        assert m and m.lstrip("@").isidentifier() and m not in naming.reserved
        assert naming.file_name(words) and "/" not in naming.file_name(words)
        assert naming.exception_name(words).isidentifier()
    assert naming.type_name("", "Fallback") == "Fallback"
    assert naming.method_name("", "") and naming.method_name("Execute", "") == naming.method_name("", "Execute")
    assert naming.method_name("Handle", "funding event") != naming.method_name("", "funding event")
    assert naming.type_name("Lookup Dealer") == naming.type_name("lookup dealer") == naming.type_name("LookupDealer")
    for word in naming.reserved:
        assert naming.escape(word) != word and naming.escape(naming.escape(word)) not in naming.reserved
    assert naming.config_key("dealerfunding", "DB_URL") != naming.config_key("dealerfunding", "DB_USER")
    assert naming.connection_key("FundingDB")
    assert all(isinstance(getattr(naming, a), str) and getattr(naming, a)
               for a in ("platform", "config_file", "messaging_actor"))
    words = {"app": "gmf.credit", "process": "Dealer Funding Request", "activity": "Lookup Dealer",
             "starter": "Receive", "resource": "EquifaxClient", "fault": "DealerInactiveFault",
             "target": "Audit Log", "destination": "gmf.funding.events"}
    for name in PATTERNS:
        file, cls, method = naming.row_names(name, words)
        assert file and cls and method and "{" not in file + cls + method
    with pytest.raises(KeyError):
        naming.row_names("no.such.pattern", words)
    kinds = ("api", "core", "infrastructure", "tests")
    folders = [naming.layer_folder(k, naming.type_name("gmf.credit", "App")) for k in kinds]
    assert len(set(folders)) == 4
    scaffold = naming.layout_scaffold(dict(zip(kinds, folders, strict=True)), "Proc", set(PATTERNS))
    assert set(scaffold) <= set(kinds)


@pytest.mark.parametrize("key", [s.key for s in all_stacks()])
def test_naming_contract_holds_for_every_registered_stack(key):
    _naming_contract(get_stack(key).naming)


def test_naming_contract_holds_for_a_stand_in_stack():
    _naming_contract(SnakeNaming())


def test_design_takes_every_name_from_the_stack(monkeypatch, gmf_app, gmf_processes, rules, findings, cards):
    """No .NET string or casing leaks into another stack's skeleton: placeholders, keys, the broker,
    the ignored-config reason and the scaffold all come from its naming."""
    import engine.stacks as registry

    class SnakeStack:
        key = "python"
        naming = SnakeNaming()

    monkeypatch.setitem(registry._FACTORIES, "python", SnakeStack)
    monkeypatch.delitem(registry._INSTANCES, "python", raising=False)
    rec = build_design_skeleton(gmf_processes[DFR], gmf_app, rules[DFR], findings[DFR], cards[DFR], stack="python")
    blob = json.dumps(rec.model_dump(mode="json"))
    for dotnet in (".NET", "appsettings.json", "ServiceBus", "ConnectionStrings:", "Gmf:", ".cs\"", "Async\"",
                   ".csproj"):
        assert dotnet not in blob, dotnet
    row = next(r for r in rec.mapping if r.activity_id == "LookupDealer" and r.pattern == "jdbc.query")
    # raw words reach the stack: its method and file casing, not a pre-cased PascalCase name
    assert (row.file, row.class_name, row.method) == ("jdbc_query/dealer_funding_request.snk",
                                                      "DealerFundingRequestJdbcQuery", "run_lookup_dealer")
    assert row.project == "gmfcredit/infrastructure"
    assert rec.sequence[1].label == "run_dealer_funding_request"  # the service entry, from row_names("end")
    assert rec.sequence[0].from_ == "Broker"
    assert {c.key for c in rec.config} and all(c.key.startswith(("GMF_", "DB_")) for c in rec.config)
    assert all("Snake" in i.reason and "settings.toml" in i.reason for i in rec.config_ignored)
    core = next(p for p in rec.solution.projects if p.kind == "core")
    assert "DealerFundingRequest.main.snk" in core.files
    assert all(cm.file.startswith("contracts/") and cm.file.endswith(".snk") for cm in rec.contracts)


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
