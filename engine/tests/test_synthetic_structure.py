"""Fidelity and content checks for the synthetic GMF-style BusinessWorks estate."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import bw6_structure_check
import pytest
from lxml import etree

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTH = REPO_ROOT / "samples" / "synthetic-gmf"
TN2018 = REPO_ROOT / "samples" / "public" / "GMF_PoC_1_BW6_CreditApp_TN2018" / "TN2018_CreditApp"
PUBLIC_REFERENCE = [
    TN2018 / "CreditCheckBackendService/CreditCheckService/Processes/creditcheckservice/"
    "LookupDatabase.bwp",
    TN2018 / "CreditAppService/CreditApp.module/Processes/creditapp/module/MainProcess.bwp",
]

EXPECTED_IDS = {
    "creditorigination.CreditApplicationIntake",
    "creditorigination.BureauOrchestrator",
    "creditorigination.DecisionEngine",
    "creditorigination.ValidateVIN",
    "creditorigination.AuditLog",
    "dealerfunding.DealerFundingRequest",
    "dealerfunding.FundingStatusLookup",
    "loanservicing.PaymentPosting",
    "loanservicing.AccountLookup",
    "loanservicing.NightlyDelinquencyScan",
    "LegacyAddressStandardize",
}
MODULES = ("CreditOrigination", "DealerFunding", "LoanServicing")

BPWS = bw6_structure_check.BPWS
TIBEX = bw6_structure_check.TIBEX
BWEXT = bw6_structure_check.BWEXT


def _bwp_files() -> list[Path]:
    return sorted(SYNTH.rglob("*.bwp"))


def _process_id(path: Path) -> str:
    if path.suffix == ".process":
        return path.stem
    parts = path.parts
    idx = len(parts) - 1 - list(reversed(parts)).index("Processes")
    rel = parts[idx + 1:]
    return ".".join([*rel[:-1], Path(rel[-1]).stem])


def _process_paths() -> dict[str, Path]:
    found = [*_bwp_files(), *SYNTH.rglob("*.process")]
    return {_process_id(p): p for p in found}


def _estate() -> dict:
    return json.loads((SYNTH / "estate.json").read_text(encoding="utf-8"))


def _activity(path: Path, name: str) -> etree._Element:
    root = etree.parse(str(path)).getroot()
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("name") == name and el.get(f"{{{TIBEX}}}xpdlId"):
            return el
    raise AssertionError(f"activity {name} not found in {path.name}")


def _sql(el: etree._Element) -> str:
    for v in el.iter("value"):
        if v.get("sqlStatement"):
            return v.get("sqlStatement")
    raise AssertionError(f"no sqlStatement on {el.get('name')}")


def _bindings(el: etree._Element) -> str:
    texts = []
    for b in el.iter(f"{{{TIBEX}}}inputBinding", f"{{{TIBEX}}}partBinding"):
        texts.append(b.get("expression") or b.text or "")
    return "\n".join(texts)


def _conditions(path: Path) -> list[str]:
    root = etree.parse(str(path)).getroot()
    return [(c.text or "").strip() for c in root.iter(f"{{{BPWS}}}transitionCondition")]


# --- structure checker ------------------------------------------------------


@pytest.mark.parametrize("path", PUBLIC_REFERENCE, ids=lambda p: p.name)
def test_check_passes_on_public_reference(path):
    assert bw6_structure_check.check(path) == []


def test_synthetic_estate_has_ten_bwp_files():
    assert len(_bwp_files()) == 10


@pytest.mark.parametrize("path", _bwp_files(), ids=lambda p: p.stem)
def test_synthetic_bwp_structure(path):
    assert bw6_structure_check.check(path) == []


def _mutate(tmp_path: Path, old: str, new: str) -> Path:
    src = PUBLIC_REFERENCE[0]
    text = src.read_text(encoding="utf-8")
    assert old in text
    target = tmp_path / "Module" / "Processes" / "creditcheckservice" / src.name
    target.parent.mkdir(parents=True)
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return target


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ('tibex:xpdlId="b75f079e-d363-4c28-9b66-44009f6eacf8" ', "", "activity:"),
        ('<bpws:target linkName="StartToEnd"/>', "", "links:"),
        ("string-length($QueryRecords/Record[1]/rating)>0]]", "]]", "links:"),
        ('tibex:linkType="SUCCESSWITHNOCONDITION"', 'tibex:linkType="SUCCESS"', "links:"),
        ('name="QueryRecords-input" sca-bpel:internal', 'name="Renamed" sca-bpel:internal',
         "variables:"),
        ('sharedResourceProperty="jdbcProperty"', 'sharedResourceProperty="nope"', "resources:"),
        ("<tibex:ProcessInfo ", "<tibex:ProcessInfoX ", "root:"),
        ('name="creditcheckservice.LookupDatabase"', 'name="other.Name"', "root:"),
        ("&lt;xsl:template name=&quot;End-input&quot;", "&lt;xsl:template name=&quot;End-input",
         "bindings:"),
    ],
)
def test_check_reports_broken_structure(tmp_path, old, new, rule):
    problems = bw6_structure_check.check(_mutate(tmp_path, old, new))
    assert any(p.startswith(rule) for p in problems), problems


def test_check_cli_exit_codes(tmp_path):
    assert bw6_structure_check.main([str(PUBLIC_REFERENCE[0])]) == 0
    broken = _mutate(tmp_path, "<tibex:ProcessInfo ", "<tibex:ProcessInfoX ")
    assert bw6_structure_check.main([str(broken)]) == 1


def _saxon():
    saxonche = pytest.importorskip("saxonche")
    return saxonche.PySaxonProcessor(license=False)


def _compile_errors(proc, text: str) -> str | None:
    from saxonche import PySaxonApiError

    xslt = proc.new_xslt30_processor()
    try:
        xslt.compile_stylesheet(stylesheet_text=text)
    except PySaxonApiError as exc:
        return str(exc)
    return None


@pytest.mark.parametrize("path", _bwp_files(), ids=lambda p: p.stem)
def test_synthetic_mappings_compile_as_xslt2(path):
    root = etree.parse(str(path)).getroot()
    bindings = list(root.iter(f"{{{TIBEX}}}inputBinding", f"{{{TIBEX}}}partBinding"))
    assert bindings
    with _saxon() as proc:
        for b in bindings:
            err = _compile_errors(proc, b.get("expression") or b.text or "")
            assert err is None, f"line {b.sourceline}: {err}"


@pytest.mark.parametrize("path", _bwp_files(), ids=lambda p: p.stem)
def test_synthetic_conditions_compile_as_xpath2(path):
    root = etree.parse(str(path)).getroot()
    namespaces = {i.get("prefix"): i.get("namespace")
                  for i in root.iter(f"{{{TIBEX}}}namespaceItem")}
    namespaces["xsd"] = "http://www.w3.org/2001/XMLSchema"
    decls = " ".join(f'xmlns:{p}="{u}"' for p, u in namespaces.items())
    with _saxon() as proc:
        for cond in root.iter(f"{{{BPWS}}}transitionCondition"):
            expr = (cond.text or "").strip()
            if expr == "##otherwise##":
                continue
            names = sorted(set(re.findall(r"\$([A-Za-z_][\w.]*)", expr)))
            params = "".join(f'<xsl:param name="{n}"/>' for n in names)
            select = expr.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
            sheet = (f'<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" {decls} '
                     f'version="2.0">{params}<xsl:template name="t">'
                     f'<xsl:value-of select="{select}"/></xsl:template></xsl:stylesheet>')
            err = _compile_errors(proc, sheet)
            assert err is None, f"line {cond.sourceline}: {expr}: {err}"


# --- estate layout ----------------------------------------------------------


def test_expected_processes_present():
    assert set(_process_paths()) == EXPECTED_IDS


def test_estate_layout():
    assert "synthetic" in (SYNTH / "SYNTHETIC.txt").read_text(encoding="utf-8").lower()
    app = SYNTH / "gmf.credit.application" / "META-INF"
    assert (app / "MANIFEST.MF").is_file() and (app / "TIBCO.xml").is_file()
    tibco = (app / "TIBCO.xml").read_text(encoding="utf-8")
    for module in MODULES:
        meta = SYNTH / module / "META-INF"
        for name in ("MANIFEST.MF", "module.bwm", "default.substvar"):
            assert (meta / name).is_file(), f"{module}/META-INF/{name}"
        assert f"<packaging:symbolicName>{module}</packaging:symbolicName>" in tibco
        for folder in ("Processes", "Schemas", "Service Descriptors", "Resources", "Policies"):
            assert (SYNTH / module / folder).is_dir(), f"{module}/{folder}"
    legacy = SYNTH / "LegacyAddress"
    for rel in ("LegacyAddressStandardize.process", "defaultVars/defaultVars.substvar",
                "AddressService.sharedhttp", "Schemas/Address.xsd"):
        assert (legacy / rel).is_file(), rel
    for rel in ("Database/schema.sql", "Database/seed.sql", "Stubs/equifax.json",
                "Stubs/transunion.json"):
        assert (SYNTH / rel).is_file(), rel


def test_resource_files_present():
    kinds = {p.suffix for p in SYNTH.rglob("Resources/*/*")}
    assert {".jdbcResource", ".httpClientResource", ".jmsConnResource"} <= kinds


def test_all_xml_is_well_formed():
    exts = {".bwp", ".bwm", ".substvar", ".xsd", ".wsdl", ".process", ".sharedhttp",
            ".jdbcResource", ".httpClientResource", ".jmsConnResource", ".httpConnResource",
            ".xml", ".jsv", ".msv"}
    files = [p for p in SYNTH.rglob("*") if p.suffix in exts]
    assert files
    for p in files:
        etree.parse(str(p))
    for p in SYNTH.rglob("*.json"):
        json.loads(p.read_text(encoding="utf-8"))


def test_no_markdown_and_no_em_dashes():
    assert not list(SYNTH.rglob("*.md"))
    for p in SYNTH.rglob("*"):
        if p.is_file():
            assert "\u2014" not in p.read_text(encoding="utf-8", errors="ignore"), p


def test_bw5_dialect():
    path = SYNTH / "LegacyAddress" / "LegacyAddressStandardize.process"
    root = etree.parse(str(path)).getroot()
    pd = "http://xmlns.tibco.com/bw/process/2003"
    assert root.tag == f"{{{pd}}}ProcessDefinition"
    starter = root.find(f"{{{pd}}}starter")
    assert starter.findtext(f"{{{pd}}}type") == "com.tibco.plugin.http.HTTPEventSource"
    assert root.findall(f"{{{pd}}}activity")
    conds = [t.findtext(f"{{{pd}}}conditionType") for t in root.iter(f"{{{pd}}}transition")]
    assert "xpath" in conds
    assert root.find(f".//{{{pd}}}inputBindings/*") is not None


# --- seeded rules and findings ------------------------------------------------


def test_estate_json_oracle():
    estate = _estate()
    paths = _process_paths()
    ids = [p["id"] for p in estate["processes"]]
    assert set(ids) == EXPECTED_IDS and len(ids) == len(EXPECTED_IDS)
    for proc in estate["processes"]:
        assert set(proc) >= {"id", "module", "trigger", "rules", "findings", "tables_read",
                             "tables_written", "queues_in", "queues_out"}
        text = paths[proc["id"]].read_text(encoding="utf-8").lower()
        for table in proc["tables_read"] + proc["tables_written"]:
            assert f"gmf.{table}" in text, (proc["id"], table)
        for queue in proc["queues_in"] + proc["queues_out"]:
            assert queue in text, (proc["id"], queue)
        for item in proc["rules"] + proc["findings"]:
            assert item["id"] and item["text"]


def test_queue_and_topic_names():
    paths = _process_paths()
    funding = paths["dealerfunding.DealerFundingRequest"].read_text(encoding="utf-8")
    assert 'destination="gmf.dealer.funding.request"' in funding
    assert 'destination="gmf.funding.events"' in funding and 'messagingStyle="Topic"' in funding
    payments = paths["loanservicing.PaymentPosting"].read_text(encoding="utf-8")
    assert 'destination="gmf.payments.inbound"' in payments
    nightly = paths["loanservicing.NightlyDelinquencyScan"].read_text(encoding="utf-8")
    assert 'destination="gmf.collections.queue"' in nightly
    for text in (funding, payments):
        assert re.search(r'messageSelector="[^"]+"', text)


def test_seed_intake_duplicate_check_and_unused_response_field():
    path = _process_paths()["creditorigination.CreditApplicationIntake"]
    assert re.search(r"where ssn = \?", _sql(_activity(path, "CheckDuplicateApplication")))
    body = path.read_text(encoding="utf-8")
    assert "transUnionScore" not in body
    schema = (SYNTH / "CreditOrigination" / "Schemas" / "CreditApplication.xsd").read_text()
    assert 'name="transUnionScore"' in schema
    swagger = json.loads((SYNTH / "CreditOrigination" / "Service Descriptors" /
                          "creditorigination.CreditApplicationIntake-applications.json")
                         .read_text(encoding="utf-8"))
    props = swagger["definitions"]["CreditApplicationResponse"]["properties"]
    assert "transUnionScore" in props


def test_seed_dealer_funding_like_and_no_transaction():
    path = _process_paths()["dealerfunding.DealerFundingRequest"]
    assert re.search(r"where dealer_id like \?", _sql(_activity(path, "LookupDealer")))
    read = _sql(_activity(path, "CheckExistingFunding")).lower()
    write = _sql(_activity(path, "InsertFunding")).lower()
    assert "from gmf.funding" in read and "insert into gmf.funding" in write
    assert "tibex:group=" not in path.read_text(encoding="utf-8")


def test_seed_payment_posting_transaction_group_and_grace():
    path = _process_paths()["loanservicing.PaymentPosting"]
    root = etree.parse(str(path)).getroot()
    groups = [s for s in root.iter(f"{{{BPWS}}}scope")
              if s.get(f"{{{TIBEX}}}group") == "localTransaction"]
    assert len(groups) == 1
    inside = {el.get("name") for el in groups[0].iter() if isinstance(el.tag, str)}
    assert {"LoadAccount", "InsertPayment", "UpdateLoanAccount"} <= inside
    assert any(">= 10" in c for c in _conditions(path))
    assert "25.00" in _bindings(_activity(path, "AssessLateFee"))


def test_seed_decision_engine_tiers_and_dti():
    path = _process_paths()["creditorigination.DecisionEngine"]
    conds = _conditions(path)
    for threshold in ("740", "680", "620"):
        assert any(threshold in c for c in conds), threshold
    assert conds.count("##otherwise##") == 2  # missing-income guard and decline branch
    dti = "number($Start/parameters/monthlyDebt) div number($Start/parameters/monthlyIncome)"
    assert dti in _bindings(_activity(path, "ComputeDTI"))
    assert "72" in _bindings(_activity(path, "AssignTierC"))


def test_seed_faults():
    paths = _process_paths()
    expected = {
        "creditorigination.CreditApplicationIntake": "DuplicateApplicationFault",
        "creditorigination.DecisionEngine": "MissingIncomeFault",
        "creditorigination.BureauOrchestrator": "BureauUnavailableFault",
        "dealerfunding.DealerFundingRequest": "DealerInactiveFault",
    }
    for pid, fault in expected.items():
        root = etree.parse(str(paths[pid])).getroot()
        names = [t.get("faultName", "").split(":")[-1] for t in root.iter(f"{{{BPWS}}}throw")]
        assert fault in names, pid


def test_seed_bureau_parallel_flow_and_resilience():
    path = _process_paths()["creditorigination.BureauOrchestrator"]
    root = etree.parse(str(path)).getroot()
    group = next(s for s in root.iter(f"{{{BPWS}}}scope") if s.get(f"{{{TIBEX}}}group"))
    calls = [a for a in group.iter(f"{{{BWEXT}}}BWActivity")
             if a.get("activityTypeID") == "bw.http.sendHTTPRequest"]
    assert len(calls) == 2
    for name in ("EquifaxClient", "TransUnionClient"):
        res = (SYNTH / "CreditOrigination" / "Resources" / "creditorigination" /
               f"{name}.httpClientResource").read_text(encoding="utf-8")
        assert 'cmdExecutionIsolationTimeout="1000"' in res
        assert 'cmdCircuitBreakerErrorThresholdPercentage="50"' in res
        assert 'cmdCircuitBreakerRequestVolumeThreshold="20"' in res


def test_seed_audit_log_writes_ssn():
    path = _process_paths()["creditorigination.AuditLog"]
    act = _activity(path, "WriteAuditRecord")
    assert "ssn" in _sql(act).lower()
    assert "/tns:ssn" in _bindings(act)


def test_seed_account_lookup_masks_ssn():
    path = _process_paths()["loanservicing.AccountLookup"]
    assert "concat('***-**-', substring(" in path.read_text(encoding="utf-8").replace(
        "&apos;", "'")


def test_seed_nightly_timer_and_hardcoded_endpoint():
    path = _process_paths()["loanservicing.NightlyDelinquencyScan"]
    timer = _activity(path, "DailyAt0200")
    value = next(v for v in timer.iter("value") if v.get("intervalUnit"))
    assert value.get("intervalUnit") == "Day" and value.get("timeInterval") == "1"
    assert "T02:00:00" in value.get("startTime")
    assert re.search(r"http://[a-z0-9.-]+:\d+/", _bindings(_activity(path, "SendToCollections")))
    assert "days_past_due >= 30" in _sql(_activity(path, "FindDelinquentAccounts"))


def test_seed_legacy_address_has_no_otherwise():
    text = (SYNTH / "LegacyAddress" / "LegacyAddressStandardize.process").read_text()
    assert "<pd:conditionType>xpath</pd:conditionType>" in text
    assert "otherwise" not in text


def test_seed_vin_rule():
    path = _process_paths()["creditorigination.ValidateVIN"]
    assert any("= 17" in c for c in _conditions(path))


# --- database -----------------------------------------------------------------


def test_sql_loads():
    psycopg = pytest.importorskip("psycopg")
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://exiqo:exiqo@localhost:5432/exiqo_test")
    try:
        conn = psycopg.connect(url, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"test database unavailable: {exc}")
    with conn:
        conn.execute("DROP SCHEMA IF EXISTS gmf CASCADE")
        conn.execute("CREATE SCHEMA gmf")
        conn.execute((SYNTH / "Database" / "schema.sql").read_text(encoding="utf-8"))
        conn.execute((SYNTH / "Database" / "seed.sql").read_text(encoding="utf-8"))
        counts = {
            t: conn.execute(f"select count(*) from gmf.{t}").fetchone()[0]
            for t in ("dealer", "loan_account", "credit_application")
        }
        tables = {r[0] for r in conn.execute(
            "select table_name from information_schema.tables where table_schema = 'gmf'")}
    assert counts == {"dealer": 5, "loan_account": 8, "credit_application": 6}
    assert tables == {"applicant", "vehicle", "credit_application", "bureau_pull", "decision",
                      "dealer", "deal", "funding", "loan_account", "payment", "delinquency",
                      "audit_log"}
