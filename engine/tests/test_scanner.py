"""Tests for the estate scanner (Task 6): application discovery, duplicates, both parsers."""
import json
import zipfile
from pathlib import Path

from engine.tibco_parser import scanner

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
PUBLIC = REPO_ROOT / "samples" / "public"
ESTATE = json.loads((GMF / "estate.json").read_text(encoding="utf-8"))


def _apps(result):
    return {a.id: a for a in result.applications}


def test_scan_synthetic():
    result = scanner.scan_folder(GMF)
    assert result.synthetic is True
    assert result.report.ok, result.report
    assert len(result.applications) == 2
    apps = _apps(result)
    assert set(apps) == {"gmf.credit", "LegacyAddress"}

    for oracle in ESTATE["applications"]:
        app = apps[oracle["id"]]
        assert app.dialect == oracle["dialect"]
        assert app.modules == oracle["modules"]

    procs = [p for a in result.applications for p in a.processes]
    assert len(procs) == 11
    assert {p.id for p in procs} == {p["id"] for p in ESTATE["processes"]}
    by_id = {p.id: p for p in procs}
    for oracle in ESTATE["processes"]:
        assert by_id[oracle["id"]].module == oracle["module"]
    assert {p.application for p in apps["gmf.credit"].processes} == {"gmf.credit"}
    assert {p.dialect for p in apps["LegacyAddress"].processes} == {"bw5"}

    legacy = apps["LegacyAddress"]
    assert [r.id for r in legacy.resources] == ["LegacyAddress/AddressService"]
    assert {v.name for v in legacy.substvars} >= {"AddressServicePort", "HttpHost"}
    assert [c.id for c in legacy.contracts] == ["LegacyAddress/Schemas/Address.xsd"]

    credit = apps["gmf.credit"]
    assert {r.kind for r in credit.resources} >= {"jdbc", "http", "jms"}
    assert any(c.kind == "json" for c in credit.contracts)
    assert any(c.kind == "xsd" for c in credit.contracts)
    assert any(v.source_path.endswith("gmf.credit.application/META-INF/default.substvar") for v in credit.substvars)
    # estate-level SQL goes only to applications that use JDBC
    assert credit.sql_files == ["Database/schema.sql", "Database/seed.sql"]
    assert legacy.sql_files == []
    assert result.sql_files == ["Database/schema.sql", "Database/seed.sql"]

    kinds = [f.kind for f in result.files]
    assert kinds.count("bwp") == 10
    assert kinds.count("process") == 1
    assert kinds.count("bwm") == 3
    assert all(f.duplicate_of is None for f in result.files)
    entry = next(f for f in result.files if f.path == "LegacyAddress/LegacyAddressStandardize.process")
    assert entry.dialect == "bw5" and len(entry.sha256) == 64
    assert next(f for f in result.files if f.path.endswith("CreditApplicationIntake.bwp")).dialect == "bw6"
    assert next(f for f in result.files if f.path == "Database/schema.sql").kind == "sql"
    # every parsed process file is counted once in the report
    assert result.report.files_parsed >= 11


def test_scan_flags_packaged_duplicates(tmp_path):
    with zipfile.ZipFile(PUBLIC / "GMF_PoC_1_BW6_CreditApp_TN2018.zip") as zf:
        zf.extractall(tmp_path)
    result = scanner.scan_folder(tmp_path)
    assert result.synthetic is False
    dups = [f for f in result.files if f.duplicate_of]
    assert len(dups) == 2
    for d in dups:
        assert d.kind == "bwp"
        assert "CreditCheckService.application/" in d.path
        assert ".application/" not in d.duplicate_of
        source = next(f for f in result.files if f.path == d.duplicate_of)
        assert source.sha256 == d.sha256

    procs = [p for a in result.applications for p in a.processes]
    assert not any(".application" in Path(p.source_path).as_posix() for p in procs)
    assert len({p.source_path for p in procs}) == len(procs) == 7
    assert result.report.failures == []

    apps = _apps(result)
    assert apps["CreditCheckService"].modules == ["CreditCheckService"]
    assert apps["CreditApp"].modules == ["CreditApp.module"]
    assert apps["ExperianService"].modules == ["ExperianService.module"]
    assert any("LoggingService" in a.modules for a in result.applications)
    assert {a.dialect for a in result.applications} == {"bw6"}
    assert any(s.endswith("Database/create.sql") for a in result.applications for s in a.sql_files)


def test_scan_public_bw5_and_bw6_mixed():
    result = scanner.scan_folder(PUBLIC / "GMF_PoC_2_BW5_BW6_tibcobwblog")
    assert result.report.failures == []
    bw5 = [a for a in result.applications if a.dialect == "bw5"]
    bw6 = [a for a in result.applications if a.dialect == "bw6"]
    assert sum(len(a.processes) for a in bw5) == 30
    assert len(bw5) == 15  # TIBCO_BW_REST_WADL_PROBLEM has no .process, so it is not a project
    assert bw6 and all(a.modules for a in bw6)
    assert all(p.application == a.id for a in result.applications for p in a.processes)


def test_scan_single_project_or_module_as_root():
    legacy = scanner.scan_folder(GMF / "LegacyAddress")
    assert [(a.id, a.dialect, len(a.processes)) for a in legacy.applications] == [("LegacyAddress", "bw5", 1)]
    assert legacy.report.ok and legacy.synthetic is False

    module = scanner.scan_folder(GMF / "DealerFunding")
    assert [(a.id, a.modules) for a in module.applications] == [("DealerFunding", ["DealerFunding"])]
    assert module.applications[0].processes
    assert module.report.ok


def test_scan_records_broken_file_and_goes_on(tmp_path):
    import shutil

    root = tmp_path / "LegacyAddress"
    shutil.copytree(GMF / "LegacyAddress", root)
    (root / "Broken.process").write_text("<pd:ProcessDefinition", encoding="utf-8")
    result = scanner.scan_folder(root)
    assert [len(a.processes) for a in result.applications] == [1]
    assert [f.path.endswith("Broken.process") for f in result.report.failures] == [True]
    assert not result.report.ok


def test_packaged_module_without_processes_is_not_a_duplicate(tmp_path):
    import shutil

    src = GMF / "DealerFunding"
    shutil.copytree(src, tmp_path / "DealerFunding")
    packaged = tmp_path / "gmf.application" / "DealerFunding"
    shutil.copytree(src / "META-INF", packaged / "META-INF")  # a module shell with no .bwp
    result = scanner.scan_folder(tmp_path)
    assert all(f.duplicate_of is None for f in result.files)
    assert not any("packaged copy" in e.reason for e in result.report.explained)
