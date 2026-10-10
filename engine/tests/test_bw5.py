"""Tests for the BW5 .process parser (Task 6).

The synthetic estate oracle (samples/synthetic-gmf/estate.json) is the source of
truth for the LegacyAddress process; the public BW5 projects are breadth tests.
"""
import json
import shutil
from pathlib import Path

import pytest
from lxml import etree

from engine.tibco_parser import bw5, resources
from engine.tibco_parser.completeness import CompletenessCollector, UnknownType

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
PUBLIC = REPO_ROOT / "samples" / "public"
ESTATE = json.loads((GMF / "estate.json").read_text(encoding="utf-8"))
ORACLE = {p["id"]: p for p in ESTATE["processes"] if p["file"].endswith(".process")}
LEGACY = GMF / "LegacyAddress"
LEGACY_PROCESS = LEGACY / "LegacyAddressStandardize.process"

PUBLIC_BW5 = sorted(
    p
    for d in (PUBLIC / "GMF_PoC_3_BW5_codereview_Apache2", PUBLIC / "GMF_PoC_2_BW5_BW6_tibcobwblog" / "bw5.x")
    if d.is_dir()
    for p in d.glob("**/*.process")
)


def _project_root(path: Path) -> Path:
    for anc in path.parents:
        if (anc / "vcrepo.dat").is_file() or (anc / "defaultVars").is_dir():
            return anc
    return path.parent


def _parse(path: Path, collector: CompletenessCollector | None = None):
    collector = collector or CompletenessCollector()
    root = _project_root(path)
    return bw5.parse_process(path, root.name, collector, project_root=root), collector


def _by_id(items):
    return {i.id: i for i in items}


def _nodes(proc) -> set[str]:
    return {a.id for a in proc.activities} | {g.id for g in proc.groups}


# ------------------------------------------------------------- synthetic estate
def test_bw5_legacy_address():
    proc, collector = _parse(LEGACY_PROCESS)
    report = collector.report()
    oracle = ORACLE["LegacyAddressStandardize"]

    assert report.ok, report
    assert report.files_parsed == 1
    assert proc.id == oracle["id"] == "LegacyAddressStandardize"
    assert proc.dialect == "bw5"
    assert proc.application == "LegacyAddress"
    assert proc.module == "LegacyAddress"
    assert proc.name == "LegacyAddressStandardize"
    assert proc.package == ""

    # starter: BW5 HTTP Receiver on the shared HTTP connection
    assert proc.starter is not None
    assert proc.starter.kind == oracle["trigger"]["kind"] == "http"
    assert proc.starter.activity_id == "HTTP Receiver"
    assert proc.starter.config["resource_path"] == oracle["trigger"]["resource"]
    assert proc.starter.config["resource_ref"] == resources.parse_resource(
        LEGACY / "AddressService.sharedhttp", project_root=LEGACY).id
    assert [p["name"] for p in proc.starter.config["parameters"]] == ["line1", "line2", "city", "state", "zip"]

    acts = _by_id(proc.activities)
    assert acts["HTTP Receiver"].type == "http.receive"
    assert acts["HTTP Receiver"].native_type == "com.tibco.plugin.http.HTTPEventSource"
    assert acts["Standardize Address"].type == "mapper"
    assert acts["Send HTTP Response"].type == "reply"
    assert acts["End"].type == "end"
    assert acts["Standardize Address"].source_line is not None

    # mapper bindings: one per target, function calls make them xslt
    targets = {b.target: b for b in acts["Standardize Address"].input_bindings}
    assert "StandardizedAddress/line1" in targets
    assert "upper-case" in targets["StandardizedAddress/line1"].expression
    assert targets["StandardizedAddress/line1"].language == "xslt"
    assert "StandardizedAddress/line2" in targets  # under xsl:if
    assert acts["Standardize Address"].config["input_xslt"]

    # transitions: one conditional with the state list, and NO otherwise (seeded finding LAS-F1)
    kinds = [t.kind for t in proc.transitions]
    assert kinds.count("conditional") == 1
    assert "otherwise" not in kinds
    cond = next(t for t in proc.transitions if t.kind == "conditional")
    assert cond.from_id == "Standardize Address" and cond.to_id == "Send HTTP Response"
    assert " AL AK AZ " in cond.expression and " WY " in cond.expression
    assert cond.source_line is not None
    trans_ids = {t.id for t in proc.transitions}
    for rule in oracle["rules"] + oracle["findings"]:
        where = rule["where"]
        if "activity" in where:
            assert where["activity"] in acts
        if "transition" in where:
            assert where["transition"] in trans_ids
    nodes = _nodes(proc)
    assert all(t.from_id in nodes and t.to_id in nodes for t in proc.transitions)

    assert proc.calls == []
    assert "LegacyAddress/Schemas/Address.xsd" in proc.contracts_used
    assert proc.complexity is not None
    assert len(proc.raw_xml_sha256) == 64
    assert proc.source_path.endswith("LegacyAddressStandardize.process")


def test_bw5_global_variables_stay_verbatim(tmp_path):
    root = tmp_path / "LegacyAddress"
    shutil.copytree(LEGACY, root)
    path = root / "LegacyAddressStandardize.process"
    text = path.read_text(encoding="utf-8").replace(
        "<closeConnection>true</closeConnection>", "<closeConnection>%%CloseIt%%</closeConnection>")
    path.write_text(text, encoding="utf-8")
    proc, _ = _parse(path)
    reply = _by_id(proc.activities)["Send HTTP Response"]
    assert reply.config["settings"]["closeConnection"] == "%%CloseIt%%"


def test_bw5_process_id_from_nested_path(tmp_path):
    root = tmp_path / "LegacyAddress"
    shutil.copytree(LEGACY, root)
    (root / "Sub" / "Folder").mkdir(parents=True)
    moved = root / "Sub" / "Folder" / "Name.process"
    shutil.copy(root / "LegacyAddressStandardize.process", moved)
    proc, _ = _parse(moved)
    assert proc.id == "Sub.Folder.Name"
    assert proc.package == "Sub.Folder"
    assert proc.name == "Name"


def test_bw5_unknown_type_element_and_failure(tmp_path):
    root = tmp_path / "LegacyAddress"
    shutil.copytree(LEGACY, root)
    path = root / "LegacyAddressStandardize.process"
    text = path.read_text(encoding="utf-8")
    text = text.replace("com.tibco.plugin.mapper.MapperActivity", "com.example.MysteryActivity", 1)
    text = text.replace("<pd:processVariables/>", "<pd:processVariables/><pd:bogus>x</pd:bogus>", 1)
    path.write_text(text, encoding="utf-8")
    proc, collector = _parse(path)
    report = collector.report()
    assert not report.ok
    assert UnknownType(process_id="LegacyAddressStandardize", native_type="com.example.MysteryActivity") in \
        report.unknown_activity_types
    assert _by_id(proc.activities)["Standardize Address"].type == "unknown"
    assert [u.tag for u in report.unknown_elements] == ["pd:bogus"]
    assert proc.unknown_elements == report.unknown_elements

    bad = root / "Broken.process"
    bad.write_text("<pd:ProcessDefinition xmlns:pd='http://xmlns.tibco.com/bw/process/2003'>", encoding="utf-8")
    collector = CompletenessCollector()
    with pytest.raises(etree.XMLSyntaxError):
        bw5.parse_process(bad, "LegacyAddress", collector, project_root=root)
    assert collector.report().failures[0].path == str(bad)


# ------------------------------------------------------------- public samples
def test_public_bw5_count():
    assert len(PUBLIC_BW5) == 96


@pytest.mark.parametrize("path", PUBLIC_BW5, ids=lambda p: p.relative_to(PUBLIC).as_posix())
def test_bw5_public_codereview_parses(path):
    proc, collector = _parse(path)
    report = collector.report()
    assert report.failures == []
    assert report.files_parsed == 1
    assert proc.dialect == "bw5"
    # unknown types are allowed here but must be reported, never dropped
    reported = {u.native_type for u in report.unknown_activity_types}
    assert {a.native_type for a in proc.activities if a.type == "unknown"} == reported
    # every element is consumed or explained; nothing is unknown in the public BW5 set
    assert report.unknown_elements == [], report.unknown_elements
    nodes = _nodes(proc)
    assert all(t.from_id in nodes and t.to_id in nodes for t in proc.transitions)
    for g in proc.groups:
        assert set(g.config["entry_ids"]) <= nodes and set(g.config["exit_ids"]) <= nodes
    assert proc.complexity is not None


def test_public_call_targets_resolve():
    """Static Call Process targets are process ids of the same project."""
    by_root: dict[Path, list] = {}
    for p in PUBLIC_BW5:
        proc, _ = _parse(p)
        by_root.setdefault(_project_root(p), []).append(proc)
    checked = 0
    for procs in by_root.values():
        ids = {p.id for p in procs}
        for proc in procs:
            for call in proc.calls:
                assert call.target_process in ids, (proc.id, call.target_process)
                checked += 1
    assert checked >= 40


def test_public_groups_and_fault_handler():
    base = PUBLIC / "GMF_PoC_2_BW5_BW6_tibcobwblog/bw5.x/TIBCO_BW_JMS_SELECTOR"
    proc, _ = _parse(base / "CleanMyMessages.process")
    loop = next(g for g in proc.groups if g.config.get("group_type") == "repeat")
    assert loop.kind == "loop"
    assert loop.config["repeat_condition"] == "1 = 2"
    assert loop.config["entry_ids"]
    for m in loop.member_ids:
        if m in _by_id(proc.activities):
            assert _by_id(proc.activities)[m].group_id == loop.id
    # the Catch of the receive timeout ends the endless repeat loop (straight to End)
    [handler] = proc.fault_handlers
    assert handler.fault_name == "ActivityTimedOutException"
    assert handler.activity_ids == ["Catch"]
    assert _by_id(proc.activities)["Catch"].type == "catch"
    assert any(t.from_id == "Catch" and t.to_id == "End" for t in proc.transitions)

    media = next(p for p in PUBLIC_BW5 if p.name == "UploadMultipleMedia.process")
    proc, _ = _parse(media)
    [handler] = proc.fault_handlers
    assert handler.fault_name is None  # catchAll
    assert _by_id(proc.activities)[handler.activity_ids[0]].type == "catch"
    assert len(handler.activity_ids) >= 2  # the catch and the Log after it

    review = next(p for p in PUBLIC_BW5 if p.name == "All review process.process")
    proc, _ = _parse(review)
    it = _by_id(proc.groups)["Iterate over Review Rules"]
    assert it.kind == "iterate"
    assert it.config["over"]
    assert it.config["entry_ids"] == ["Call Review Rule"]
    assert it.config["exit_ids"] == ["Call Review Rule"]
    assert any(t.to_id == it.id for t in proc.transitions)
    dynamic = _by_id(proc.activities)["Call Review Rule"]
    assert dynamic.type == "call.process" and dynamic.config["dynamic"] is True


def test_public_transaction_and_jdbc_resource_ref():
    tx_files = [p for p in PUBLIC_BW5 if "TransactionGroup" in p.read_text(encoding="utf-8", errors="replace")]
    assert tx_files
    proc, _ = _parse(tx_files[0])
    assert any(g.kind == "transaction" for g in proc.groups)

    jdbc = [p for p in PUBLIC_BW5 if "jdbcSharedConfig" in p.read_text(encoding="utf-8", errors="replace")]
    assert jdbc
    proc, _ = _parse(jdbc[0])
    act = next(a for a in proc.activities if a.type.startswith("jdbc."))
    root = _project_root(jdbc[0])
    shared = root / act.config["resource_path"].lstrip("/")
    if shared.is_file():
        assert act.config["resource_ref"] == resources.parse_resource(shared, project_root=root).id
