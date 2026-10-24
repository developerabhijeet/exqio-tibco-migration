"""Tests for the pattern catalogue, the design record skeleton and contracts to C# (Task 13)."""
import re
import string
from pathlib import Path

import pytest

from engine.analysis.models import Citation, Rule
from engine.analysis.static_rules import run_static_rules
from engine.domains import graph as graph_mod
from engine.dotnet_gen import csharp_contracts, design, patterns
from engine.dotnet_gen.models import (
    Decision,
    DesignRecord,
    ExpectedDifference,
    OpenQuestion,
    compute_design_hash,
)
from engine.ir.catalogue import CANONICAL_TYPES
from engine.ir.models import Contract, SubstVar
from engine.tibco_parser.contracts import parse_contract
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
NS = "GmfCredit.Core.Contracts"


@pytest.fixture(scope="module")
def apps():
    return scan_folder(GMF).applications


@pytest.fixture(scope="module")
def graph(apps):
    return graph_mod.build_graph(apps)


def _proc(apps, name):
    for app in apps:
        for p in app.processes:
            if p.name == name:
                return p, app
    raise KeyError(name)


def _decided(process, app, decisions):
    findings = run_static_rules(process, app.contracts, app.substvars)
    out = []
    for f in findings:
        rule = f.id.split("-")[0]
        out.append(f.model_copy(update={"decision": decisions.get(rule, "keep")}))
    return out


def _design(apps, graph, name, rules=(), decisions=None, app_override=None):
    p, app = _proc(apps, name)
    app = app_override or app
    findings = _decided(p, app, decisions or {})
    card = graph_mod.io_card(p, app, graph)
    return design.build_design_skeleton(p, app, list(rules), findings, card), findings


def _rows(rec: DesignRecord, activity_id: str, pattern: str | None = None):
    rows = [r for r in rec.mapping if r.activity_id == activity_id]
    if pattern:
        rows = [r for r in rows if r.pattern == pattern]
    return rows


# ------------------------------------------------------------------ patterns
def test_every_canonical_type_has_pattern():
    assert len(CANONICAL_TYPES) == 49
    missing = sorted(CANONICAL_TYPES - set(patterns.PATTERNS))
    assert missing == []
    ctx = {k: "X" for k in patterns.TEMPLATE_KEYS}
    for key, spec in patterns.PATTERNS.items():
        assert spec.name == key
        assert spec.project in ("api", "core", "infrastructure", "tests")
        assert spec.description and spec.file_template.endswith(".cs")
        assert isinstance(spec.nuget, list)
        for tpl in (spec.file_template, spec.class_template, spec.method_template):
            used = {f for _, f, _, _ in string.Formatter().parse(tpl) if f}
            assert used <= set(patterns.TEMPLATE_KEYS), (key, tpl)
            assert tpl.format(**ctx)
    # flagged decisions the brief names
    for key in ("java.invoke", "cmd.exec", "ftp.get", "ftp.put", "shared.get", "shared.set",
                "checkpoint", "confirm", "group.critical"):
        assert patterns.PATTERNS[key].decision is not None, key
    assert patterns.PATTERNS["java.invoke"].origin == "agent"
    assert "FluentFTP" in patterns.PATTERNS["ftp.get"].nuget
    assert "Dapper" in patterns.PATTERNS["jdbc.query"].nuget
    assert "Azure.Messaging.ServiceBus" in patterns.PATTERNS["jms.receive"].nuget
    assert "Microsoft.Extensions.Http.Resilience" in patterns.PATTERNS["http.invoke"].nuget


def test_pascal_names():
    assert design.pascal("gmf.credit") == "GmfCredit"
    assert design.pascal("LegacyAddress") == "LegacyAddress"
    assert design.pascal("Standardize Address") == "StandardizeAddress"
    assert design.pascal("get") == "Get"
    assert design.pascal("HTTP Receiver") == "HttpReceiver"
    assert design.pascal("GMF.DB.URL") == "GmfDbUrl"
    assert design.pascal("GMFFundingDB") == "GMFFundingDB"
    assert design.pascal("parallel:RenderBureauRequest") == "ParallelRenderBureauRequest"


# -------------------------------------------------------------------- design
def test_design_dealer_funding_mapping(apps, graph):
    rule = Rule(id="DFR-R1", text="The dealer must be ACTIVE.", confidence=0.9, status="approved",
                citations=[Citation(activity_id="LookupDealer", kind="condition",
                                    text="$LookupDealer/Record[1]/status = 'ACTIVE'")])
    rejected = Rule(id="DFR-RX", text="rejected", confidence=0.1, status="rejected",
                    citations=[Citation(activity_id="LookupDealer", kind="sql", text="like")])
    rec, findings = _design(apps, graph, "DealerFundingRequest", rules=[rule, rejected],
                            decisions={"S1": "fix", "S2": "keep"})
    s1 = next(f.id for f in findings if f.id.startswith("S1"))
    s2 = next(f.id for f in findings if f.id.startswith("S2"))

    assert rec.process_id == "dealerfunding.DealerFundingRequest"
    assert rec.narrative is None and re.fullmatch(r"[0-9a-f]{64}", rec.design_hash)
    assert re.fullmatch(r"[0-9a-f]{64}", rec.spec_hash)
    assert rec.solution.name == "GmfCredit"
    assert [(pr.name, pr.kind) for pr in rec.solution.projects] == [
        ("GmfCredit.Api", "api"), ("GmfCredit.Core", "core"),
        ("GmfCredit.Infrastructure", "infrastructure"), ("GmfCredit.Tests", "tests")]

    # every IR activity is mapped
    p, _ = _proc(apps, "DealerFundingRequest")
    assert {a.id for a in p.activities} <= {r.activity_id for r in rec.mapping}

    (lookup,) = _rows(rec, "LookupDealer", "jdbc.query")
    assert lookup.project == "GmfCredit.Infrastructure"
    assert lookup.file == "Repositories/DealerFundingRequestRepository.cs"
    assert lookup.class_name == "DealerFundingRequestRepository"
    assert lookup.method == "LookupDealerAsync"
    assert lookup.origin == "code"
    assert s2 in lookup.finding_ids  # the LIKE finding rides on the LIKE row
    assert lookup.rule_ids == ["DFR-R1"]  # rejected rules are not carried
    (insert,) = _rows(rec, "InsertFunding", "jdbc.update")
    assert insert.method == "InsertFundingAsync" and s1 in insert.finding_ids

    (consumer,) = _rows(rec, "ReceiveFundingRequest")
    assert consumer.class_name == "DealerFundingRequestConsumer"
    assert consumer.file == "Messaging/DealerFundingRequestConsumer.cs"
    assert consumer.project == "GmfCredit.Infrastructure"

    (publish,) = _rows(rec, "PublishFundingEvent", "jms.publish")
    assert publish.class_name == "IMessageBus"
    assert publish.method == 'PublishAsync("gmf.funding.events")'
    assert publish.project == "GmfCredit.Core" and publish.file == "Messaging/IMessageBus.cs"

    (throw,) = _rows(rec, "ThrowDealerInactive", "throw")
    assert throw.file == "Exceptions/DealerInactiveFaultException.cs"
    assert throw.class_name == "DealerInactiveFaultException"  # never the fault contract record's name
    assert any(s.to == "DealerInactiveFaultException" and "(ThrowDealerInactive)" in s.label for s in rec.sequence)

    # input mappings: a plain path and the XPath functions code can translate (concat) are code
    (m1,) = _rows(rec, "LookupDealer", "input.mapping")
    assert m1.origin == "code" and m1.method == "LookupDealerMapping"
    assert m1.file == "Mappings/DealerFundingRequestMappings.cs" and m1.project == "GmfCredit.Core"
    (m2,) = _rows(rec, "LookupDeal", "input.mapping")
    assert m2.origin == "code"

    files = {pr.kind: pr.files for pr in rec.solution.projects}
    assert "Repositories/DealerFundingRequestRepository.cs" in files["infrastructure"]
    assert {"Messaging/ServiceBusMessageBus.cs", "Messaging/InMemoryMessageBus.cs"} <= set(files["infrastructure"])
    assert "Messaging/IMessageBus.cs" in files["core"]
    assert "Services/DealerFundingRequestService.cs" in files["core"]
    assert "DealerFundingRequestRulesTests.cs" in files["tests"]

    msg = {(m.kind, m.name, m.direction) for m in rec.messaging}
    assert ("queue", "gmf.dealer.funding.request", "in") in msg
    assert ("topic", "gmf.funding.events", "out") in msg

    assert [d.finding_id for d in rec.expected_differences] == [s1]

    contracts = {c.contract_id: c.record_names for c in rec.contracts}
    assert contracts["DealerFunding/Schemas/FundingRequest.xsd"] == ["FundingRequest"]
    assert "FundingEvent" in contracts["DealerFunding/Schemas/FundingEvent.xsd"]
    assert "DealerInactiveFault" in contracts["DealerFunding/Schemas/Faults.xsd"]
    assert "Contracts/DealerFunding/FundingRequest.cs" in files["core"]
    cmap = {c.contract_id: c for c in rec.contracts}
    assert cmap["DealerFunding/Schemas/FundingRequest.xsd"].namespace == "GmfCredit.Core.Contracts.DealerFunding"
    assert cmap["DealerFunding/Schemas/FundingRequest.xsd"].file == "Contracts/DealerFunding/FundingRequest.cs"

    # rule and finding ids ride on the primary row; a mapping row only gets ids whose
    # expression citation quotes one of its bindings
    (m_insert,) = _rows(rec, "InsertFunding", "input.mapping")
    assert m_insert.finding_ids == [] and m_insert.rule_ids == []
    assert m1.finding_ids == [s2]  # the S2 citation quotes the concat(..., '%') binding
    assert m1.rule_ids == []

    assert rec.sequence[0].from_ == "ServiceBus"
    assert rec.sequence[0].to == "DealerFundingRequestConsumer"
    labels = [s.label for s in rec.sequence]
    order = [next(i for i, lab in enumerate(labels) if a in lab)
             for a in ("LookupDealer)", "LookupDeal)", "CheckExistingFunding", "InsertFunding", "PublishFundingEvent")]
    assert order == sorted(order)

    # deterministic
    again, _ = _design(apps, graph, "DealerFundingRequest", rules=[rule, rejected],
                       decisions={"S1": "fix", "S2": "keep"})
    assert again.model_dump() == rec.model_dump()


def test_design_other_triggers(apps, graph):
    rec, _ = _design(apps, graph, "FundingStatusLookup")
    (ctl,) = _rows(rec, "get")
    assert ctl.project == "GmfCredit.Api" and ctl.file == "Controllers/FundingStatusLookupController.cs"
    assert ctl.class_name == "FundingStatusLookupController" and ctl.method == "GetAsync"
    reply = _rows(rec, "ReplyNotFound", "reply")[0]
    assert reply.class_name == "FundingStatusLookupController"
    assert rec.sequence[0].from_ == "Client" and rec.sequence[0].label.startswith("GET /funding/{dealId}")

    rec, _ = _design(apps, graph, "NightlyDelinquencyScan")
    (job,) = _rows(rec, "DailyAt0200")
    assert job.class_name == "NightlyDelinquencyScanJob" and job.file == "Jobs/NightlyDelinquencyScanJob.cs"
    (loop,) = _rows(rec, "ForEachDelinquentAccount")
    assert loop.canonical_type == "group.iterate" and loop.method == "ForEachDelinquentAccountAsync"
    msg = {(m.kind, m.name, m.direction) for m in rec.messaging}
    assert msg == {("queue", "gmf.collections.queue", "out")}
    labels = [s.label for s in rec.sequence]
    assert labels.index(next(x for x in labels if "RecordDelinquency" in x)) < labels.index(
        next(x for x in labels if "LogScanSummary" in x))

    rec, _ = _design(apps, graph, "BureauOrchestrator")
    res = {r.resource_id: r for r in rec.resilience}
    eq = res["CreditOrigination.creditorigination.EquifaxClient"]
    assert eq.timeout_ms == 1000
    assert eq.breaker == {"failure_rate": 50, "window": 20, "break_ms": 5000}
    (pull,) = _rows(rec, "PullEquifax", "http.invoke")
    assert pull.class_name == "EquifaxClient" and pull.file == "Clients/EquifaxClient.cs"
    assert pull.method == "PullEquifaxAsync"
    assert _rows(rec, "parallel:RenderBureauRequest")[0].canonical_type == "flow.parallel"

    rec, _ = _design(apps, graph, "CreditApplicationIntake")
    (call,) = _rows(rec, "CallAuditLog", "call.process")
    assert call.class_name == "AuditLogService" and call.method == "ExecuteAsync"

    rec, _ = _design(apps, graph, "PaymentPosting")
    (tx,) = _rows(rec, "PostPaymentTransaction")
    assert tx.canonical_type == "group.transaction"


def test_design_config_secret_detection(apps, graph):
    _, app = _proc(apps, "DealerFundingRequest")
    module_var = next(s for s in app.substvars if s.name == "FUNDING.HTTP.PORT")
    extra = [
        SubstVar(name="EMS.PASSWORD", value="changeit", source_path=module_var.source_path),
        SubstVar(name="PARTNER.CLIENT.SECRET", value="abc", source_path=module_var.source_path),
        SubstVar(name="//DealerFunding//PARTNER.API.KEY", value="k-123"),
        SubstVar(name="AUTH.BEARER.TOKEN", value="t", source_path=module_var.source_path),
        SubstVar(name="SB.NAMESPACE", source_path=module_var.source_path,
                 value="Endpoint=sb://gmf.servicebus.windows.net/;SharedAccessKeyName=a;SharedAccessKey=b"),
        SubstVar(name="REPORT.HOST", value="https://svc:hunter2@reports.example/", source_path=module_var.source_path),
    ]
    app2 = app.model_copy(update={"substvars": app.substvars + extra})
    rec, _ = _design(apps, graph, "DealerFundingRequest", app_override=app2)
    by_name = {c.substvar: c for c in rec.config}
    for name in ("EMS.PASSWORD", "PARTNER.CLIENT.SECRET", "PARTNER.API.KEY", "AUTH.BEARER.TOKEN",
                 "SB.NAMESPACE", "REPORT.HOST", "GMF.DB.URL"):
        assert by_name[name].secret is True, name
    for name in ("FUNDING.HTTP.PORT", "EMS.USER", "EMS.PROVIDER.URL"):
        assert by_name[name].secret is False, name
    assert by_name["EMS.PASSWORD"].key == "Gmf:DealerFunding:EmsPassword"
    assert by_name["FUNDING.HTTP.PORT"].key == "Gmf:DealerFunding:FundingHttpPort"
    assert "EQUIFAX.HOST" not in by_name  # another module's substvar
    # BW engine variables are not mapped, but listed with a reason
    assert not [c for c in rec.config if c.substvar.upper().startswith("BW.")]
    ignored = {c.substvar: c.reason for c in rec.config_ignored}
    assert "BW.HOST.NAME" in ignored and "BW.CLOUD.PORT" in ignored
    assert all("engine" in r for r in ignored.values())
    assert len([c for c in rec.config if c.substvar == "GMF.DB.URL"]) == 1
    # masked shared-resource settings become secrets too
    res_secrets = [c for c in rec.config if c.substvar.startswith("resource:") and c.secret]
    assert any(c.key == "Gmf:DealerFunding:GMFFundingDB:Password" for c in res_secrets)
    assert any(c.key == "ConnectionStrings:GMFFundingDB" for c in res_secrets)
    assert any(c.key == "Gmf:DealerFunding:GMFEMSConnection:Password" for c in res_secrets)


def test_design_hash_ignores_narrative(apps, graph):
    rec, _ = _design(apps, graph, "DealerFundingRequest")
    assert compute_design_hash(rec) == rec.design_hash
    narrated = rec.model_copy(update={"narrative": "The consumer reads the queue and funds once."})
    assert compute_design_hash(narrated) == rec.design_hash
    # the designer may refine decisions and open questions without moving the hash
    advised = rec.model_copy(update={
        "decisions": [Decision(title="Lock", choice="Row lock", alternatives=["Redis"], reason="r")],
        "open_questions": [OpenQuestion(question="q?", recommendation="r")]})
    assert compute_design_hash(advised) == rec.design_hash
    # what code decides moves it
    for update in ({"mapping": rec.mapping[1:]},
                   {"expected_differences": [ExpectedDifference(finding_id="F9", description="d")]},
                   {"config": rec.config[1:]},
                   {"sequence": rec.sequence[1:]}):
        assert compute_design_hash(rec.model_copy(update=update)) != rec.design_hash, list(update)


# ----------------------------------------------------------------- contracts
def test_xsd_to_records_intake():
    contract = parse_contract(GMF / "CreditOrigination" / "Schemas" / "CreditApplication.xsd")
    cs = csharp_contracts.xsd_to_records(contract, NS)
    assert f"namespace {NS};" in cs
    assert "public sealed record CreditApplicationRequest" in cs
    assert "public sealed record ApplicantType" in cs
    assert '[JsonPropertyName("ssn")]' in cs
    assert '[TibcoPath("/CreditApplicationRequest/applicant/ssn")]' in cs
    assert "public required string Ssn { get; init; }" in cs
    assert "public decimal? MonthlyIncome { get; init; }" in cs
    assert "public required DateOnly DateOfBirth { get; init; }" in cs
    assert "public required ApplicantType Applicant { get; init; }" in cs
    # xs:dateTime carries an offset, so the record keeps it (DateTime would drop it silently)
    assert "public required DateTimeOffset DecidedAt { get; init; }" in cs
    assert "public int? TransUnionScore { get; init; }" in cs
    assert "public bool? SingleBureau { get; init; }" in cs
    assert "\u2014" not in cs
    assert csharp_contracts.record_names(contract) == [
        "CreditApplicationRequest", "ApplicantType", "AddressType", "VehicleType",
        "CreditApplicationResponse", "ApplicantSnapshot"]
    # every property carries both attributes
    props = re.findall(r"public (?:required )?\S+ \w+ \{ get; init; \}", cs)
    assert len(props) == cs.count("[TibcoPath(") == cs.count("[JsonPropertyName(")


def test_xsd_enums_lists_and_extension():
    xsd = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:tns="urn:t" targetNamespace="urn:t">
  <xs:simpleType name="StatusType">
    <xs:restriction base="xs:string">
      <xs:enumeration value="ACTIVE"/><xs:enumeration value="ON-HOLD"/><xs:enumeration value="1st"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:complexType name="Base"><xs:sequence><xs:element name="id" type="xs:long"/></xs:sequence>
    <xs:attribute name="version" type="xs:int" use="required"/></xs:complexType>
  <xs:element name="Batch">
    <xs:complexType><xs:complexContent><xs:extension base="tns:Base"><xs:sequence>
      <xs:element name="status" type="tns:StatusType"/>
      <xs:element name="item" maxOccurs="unbounded"><xs:complexType><xs:sequence>
        <xs:element name="amount" type="xs:double"/><xs:element name="note" type="xs:string" minOccurs="0" maxOccurs="5"/>
      </xs:sequence></xs:complexType></xs:element>
      <xs:element name="batch" type="xs:string"/>
    </xs:sequence></xs:extension></xs:complexContent></xs:complexType>
  </xs:element>
</xs:schema>"""
    c = Contract(id="X/Schemas/Batch.xsd", kind="xsd", path="Batch.xsd", root_elements=["Batch"], text=xsd)
    cs = csharp_contracts.xsd_to_records(c, NS)
    assert "public enum StatusType" in cs
    assert '[JsonStringEnumMemberName("ON-HOLD")]' in cs and "OnHold," in cs
    assert '[JsonStringEnumMemberName("1st")]' in cs and "Value1st" in cs
    assert "public required StatusType Status { get; init; }" in cs
    assert "public required IReadOnlyList<Item> Item { get; init; }" in cs
    assert "public IReadOnlyList<string>? Note { get; init; }" in cs
    assert "public required long Id { get; init; }" in cs  # base type flattened
    assert '[TibcoPath("/Batch/@version")]' in cs
    assert '[TibcoPath("/Batch/item/amount")]' in cs
    assert "public required string BatchValue { get; init; }" in cs  # member named like its type
    assert csharp_contracts.record_names(c) == ["Batch", "Item", "Base", "StatusType"]


def test_json_schema_to_records_swagger():
    path = GMF / "CreditOrigination" / "Service Descriptors" / \
        "creditorigination.CreditApplicationIntake-applications.json"
    contract = parse_contract(path)
    cs = csharp_contracts.json_schema_to_records(contract, NS)
    assert "public sealed record CreditApplicationRequest" in cs
    assert "public sealed record Applicant" in cs
    assert "public Applicant? Applicant { get; init; }" in cs
    assert "public decimal? MonthlyIncome { get; init; }" in cs
    assert "public DateOnly? DateOfBirth { get; init; }" in cs
    assert "public DateTimeOffset? DecidedAt { get; init; }" in cs
    assert "public int? ModelYear { get; init; }" in cs
    assert '[TibcoPath("/CreditApplicationRequest/applicant/ssn")]' in cs
    assert '[JsonPropertyName("ssn")]' in cs
    assert csharp_contracts.to_records(contract, NS) == cs


@pytest.mark.parametrize("module", ["CreditOrigination", "DealerFunding", "LoanServicing"])
def test_all_estate_contracts_render(module):
    for path in sorted((GMF / module).rglob("*")):
        if path.suffix not in (".xsd", ".json") or "META-INF" in path.parts:
            continue
        contract = parse_contract(path)
        cs = csharp_contracts.to_records(contract, NS)
        names = csharp_contracts.record_names(contract)
        assert names, path
        for n in names:
            assert re.search(rf"public (sealed record|enum) {n}\b", cs), (path, n)


def test_xsd_recursive_element_ref():
    xsd = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="root"><xs:complexType><xs:sequence>
    <xs:element ref="layer" minOccurs="0" maxOccurs="unbounded"/></xs:sequence></xs:complexType></xs:element>
  <xs:element name="layer"><xs:complexType><xs:sequence>
    <xs:element ref="process"/><xs:element ref="layer" minOccurs="0" maxOccurs="unbounded"/>
  </xs:sequence><xs:attribute name="depth" type="xs:string"/></xs:complexType></xs:element>
  <xs:element name="process" type="xs:string"/>
</xs:schema>"""
    c = Contract(id="X/Schemas/Layers.xsd", kind="xsd", path="Layers.xsd", root_elements=["root"], text=xsd)
    cs = csharp_contracts.xsd_to_records(c, NS)
    assert csharp_contracts.record_names(c) == ["Root", "Layer"]
    assert "public IReadOnlyList<Layer>? LayerValue { get; init; }" in cs
    assert '[TibcoPath("/root/layer/@depth")]' in cs
    assert "public required string Process { get; init; }" in cs


def test_contract_plan_gmf_credit_no_collisions(apps, graph):
    _, app = _proc(apps, "DealerFundingRequest")
    plans = csharp_contracts.plan_contracts("GmfCredit", app.contracts)
    assert set(plans) == {c.id for c in app.contracts}
    files = [pl.file for pl in plans.values()]
    assert len(files) == len(set(files))
    by_ns: dict[str, list[str]] = {}
    for pl in plans.values():
        assert pl.namespace == f"GmfCredit.Core.Contracts.{pl.module}"
        assert pl.file.startswith(f"Contracts/{pl.module}/")
        by_ns.setdefault(pl.namespace, []).extend(pl.record_names)
    for ns, names in by_ns.items():
        assert len(names) == len(set(names)), ns
    # the XSD keeps the natural name; the service descriptor's copy is renamed and reported
    intake = plans["CreditOrigination/Service Descriptors/creditorigination.CreditApplicationIntake-applications.json"]
    assert intake.collisions["CreditApplicationRequest"] == "CreditApplicationRequestJson"
    assert "CreditApplicationRequest" in plans["CreditOrigination/Schemas/CreditApplication.xsd"].record_names
    # rendering with the plan's reserved names emits exactly the planned names
    contract = next(c for c in app.contracts if c.id == intake.contract_id)
    cs = csharp_contracts.to_records(contract, intake.namespace, intake.reserved)
    assert "public sealed record CreditApplicationRequestJson" in cs
    assert "public sealed record CreditApplicationRequest\n" not in cs
    assert f"namespace {intake.namespace};" in cs

    rec, _ = _design(apps, graph, "CreditApplicationIntake")
    qs = [q.question for q in rec.open_questions]
    assert any("CreditApplicationRequest" in q and "CreditApplicationRequestJson" in q for q in qs)
    rec, _ = _design(apps, graph, "FundingStatusLookup")
    assert any("FundingStatusResponseJson" in q.question for q in rec.open_questions)


def test_generated_contracts_compile(tmp_path):
    import os
    import subprocess

    import toolchain

    dotnet = toolchain.detect()["dotnet"]
    if not dotnet["present"]:
        pytest.skip("dotnet SDK not found by scripts/toolchain.py detect(); compile check skipped")
    proj = tmp_path / "contracts"
    proj.mkdir()
    (proj / "Contracts.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework>'
        "<Nullable>enable</Nullable><TreatWarningsAsErrors>true</TreatWarningsAsErrors>"
        "<ImplicitUsings>disable</ImplicitUsings></PropertyGroup></Project>\n")
    count = 0
    for path in sorted((REPO_ROOT / "samples").rglob("*")):
        if path.suffix not in (".xsd", ".wsdl", ".json") or "META-INF" in path.parts or not path.is_file():
            continue
        contract = parse_contract(path)
        ns = f"Check.C{count}"
        (proj / f"C{count}.cs").write_text(csharp_contracts.to_records(contract, ns), encoding="utf-8")
        (proj / f"A{count}.cs").write_text(
            f"namespace {ns};\n[System.AttributeUsage(System.AttributeTargets.Property)]\n"
            "public sealed class TibcoPathAttribute(string path) : System.Attribute\n"
            "{\n    public string Path { get; } = path;\n}\n", encoding="utf-8")
        count += 1
    assert count > 20
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    run = subprocess.run([dotnet["path"], "build", "-nologo", "-clp:Summary", str(proj)],
                         check=False, capture_output=True, text=True, timeout=600, env=env)
    out = run.stdout + run.stderr
    assert run.returncode == 0, out[-4000:]
    assert re.search(r"\b0 Warning\(s\)", out) and re.search(r"\b0 Error\(s\)", out), out[-4000:]


# ------------------------------------------------- the diagrams an architect reviews
def test_design_views_are_derived_from_the_record_and_the_process(apps, graph):
    """Classes come from the mapping, tables and SQL from the activities, components from the
    layout and the messaging. Nothing here is drawn by a model."""
    record, _ = _design(apps, graph, "DealerFundingRequest")
    views = record.views

    classes = {c["name"]: c for c in views["classes"]}
    assert {r.class_name for r in record.mapping} == set(classes)
    repo = classes["DealerFundingRequestRepository"]
    assert repo["stereotype"] == "repository"
    assert "LookupDealerAsync" in repo["methods"]

    tables = {t["name"]: t for t in views["tables"]}
    assert {"dealer", "deal", "funding"} <= set(tables)
    funding = tables["funding"]
    assert "insert" in funding["verbs"] and "select" in funding["verbs"]
    roles = {c["name"]: c["role"] for c in funding["columns"]}
    assert roles.get("amount") == "written"  # written by the insert
    assert roles.get("deal_id") == "filter"  # read back in the dedup where clause

    sql = {q["activity_id"]: q for q in views["sql"]}
    assert sql["InsertFunding"]["verb"] == "insert"
    assert sql["InsertFunding"]["tables"] == ["funding"]
    assert "dealId" in sql["LookupDeal"]["parameters"]

    edges = {(e["source"], e["target"]) for e in views["components"]}
    infra = next(p.name for p in record.solution.projects if p.kind == "infrastructure")
    assert (infra, "funding") in edges  # the repository reaches the table


def test_design_views_stay_out_of_the_design_hash(apps, graph):
    """A better extractor must not reset an approved G3: the views restate what is already hashed."""
    record, _ = _design(apps, graph, "DealerFundingRequest")
    before = record.design_hash
    record.views = {"classes": [], "tables": [], "sql": [], "components": []}
    assert design.compute_design_hash(record) == before
