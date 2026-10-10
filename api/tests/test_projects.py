"""Projects, uploads, scan and the process read views (Task 17)."""
from __future__ import annotations

import asyncio
import io
import shutil
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from api.app.config import REPO_ROOT as CONFIG_REPO_ROOT
from api.app.config import Settings
from api.app.models import (
    Application,
    AuditLog,
    Domain,
    Finding,
    Gate,
    GraphEdge,
    GraphNode,
    Job,
    Process,
    Rule,
    ScanReport,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = REPO_ROOT / "samples" / "synthetic-gmf"


async def _project(client, name: str = "gmf") -> str:
    r = await client.post("/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _wait_job(client, job_id: str, timeout: float = 120.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        r = await client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in ("done", "failed"):
            return job
        assert asyncio.get_running_loop().time() < deadline, f"job still {job['status']}"
        await asyncio.sleep(0.2)


@pytest.mark.parametrize("bad", ["../evil.txt", "/abs.txt", "a/../../x", "..\\x", "C:\\x",
                                 "\\\\server\\share\\x"])
async def test_upload_refuses_unsafe_zip(client, settings, bad):
    pid = await _project(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok/readme.txt", "fine")
        zf.writestr(bad, "nope")
    r = await client.post(f"/projects/{pid}/upload",
                          files={"file": ("estate.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": f"unsafe path: {bad}"}
    # nothing escaped the uploads folder and nothing was kept from the refused upload
    data = settings.data_dir
    assert not any(data.rglob("evil.txt")) and not any(data.rglob("x")) and not any(data.rglob("abs.txt"))
    assert not (data.parent / "evil.txt").exists() and not (data.parent / "x").exists()
    assert not Path("/abs.txt").exists()
    project_dir = data / "uploads" / pid
    assert not project_dir.exists() or not any(project_dir.rglob("*"))


async def test_upload_refuses_zip_symlink(client):
    pid = await _project(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    r = await client.post(f"/projects/{pid}/upload",
                          files={"file": ("estate.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400
    assert r.json() == {"detail": "unsafe path: link"}


async def test_upload_by_server_path(client, tmp_path):
    pid = await _project(client)
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(tmp_path / "missing")})
    assert r.status_code == 400
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(tmp_path)})  # holds data/
    assert r.status_code == 400 and r.json() == {"detail": "path contains the upload store"}
    src = tmp_path / "estate"
    (src / "Proc").mkdir(parents=True)
    (src / "Proc" / "a.process").write_text("<x/>")
    (src / "vcrepo.dat").write_text("")
    (src / "link.process").symlink_to(src / "Proc" / "a.process")  # symlinks are not copied
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(src)})
    assert r.status_code == 201, r.text
    files = r.json()["files"]
    assert [f["path"] for f in files] == ["Proc/a.process", "vcrepo.dat"]
    assert files[0]["kind"] == "process" and files[0]["dialect"] == "bw5" and files[0]["size"] == 4


async def test_upload_and_scan_synthetic(client, app, tmp_path):
    pid = await _project(client)
    archive = shutil.make_archive(str(tmp_path / "synthetic-gmf"), "zip",
                                  root_dir=SYNTHETIC.parent, base_dir=SYNTHETIC.name)
    data = Path(archive).read_bytes()
    r = await client.post(f"/projects/{pid}/upload",
                          files={"file": ("synthetic-gmf.zip", data, "application/zip")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["upload_id"]
    paths = {f["path"] for f in body["files"]}
    assert "SYNTHETIC.txt" in paths  # the single top folder is unwrapped
    assert "DealerFunding/Processes/dealerfunding/DealerFundingRequest.bwp" in paths

    r = await client.post(f"/projects/{pid}/scan", json={})
    assert r.status_code == 202, r.text
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["processes"] == 11

    report = (await client.get(f"/projects/{pid}/report")).json()
    assert report["synthetic"] is True
    assert report["ok"] is True
    assert report["files_parsed"] > 0 and report["unknown_activity_types"] == []

    procs = (await client.get(f"/projects/{pid}/processes")).json()
    assert len(procs) == 11
    by_ir = {p["ir_id"]: p for p in procs}
    dfr = by_ir["dealerfunding.DealerFundingRequest"]
    assert dfr["spec_hash"] and len(dfr["spec_hash"]) == 64
    assert dfr["domain_name"] == "Dealer Funding"

    async with app.state.sessionmaker() as s:
        domains = (await s.execute(select(Domain).where(Domain.project_id == pid))).scalars().all()
        assert len(domains) == 4
        assert all(d.name == d.suggested_name and d.evidence for d in domains)
        assert (await s.scalar(select(func.count()).select_from(GraphNode)
                               .where(GraphNode.project_id == pid))) > 0
        assert (await s.scalar(select(func.count()).select_from(GraphEdge)
                               .where(GraphEdge.project_id == pid))) > 0

    files = (await client.get(f"/projects/{pid}/files")).json()
    assert any(f["dialect"] == "bw6" for f in files)

    ir = (await client.get(f"/processes/{dfr['id']}")).json()
    assert ir["id"] == "dealerfunding.DealerFundingRequest" and ir["activities"]

    canvas = (await client.get(f"/processes/{dfr['id']}/canvas")).json()
    assert canvas["nodes"] and canvas["edges"]
    node = canvas["nodes"][0]
    assert set(node["data"]) >= {"name", "canonical_type", "native_type", "source_line", "config",
                                 "rule_ids", "finding_ids"}
    assert any(n["data"]["source_line"] for n in canvas["nodes"])
    conditional = [e for e in canvas["edges"] if e["kind"] == "conditional"]
    assert conditional and all(e["expression"] and e["label"] for e in conditional)
    assert any(e["kind"] == "otherwise" for e in canvas["edges"])
    assert "groups" in canvas

    card = (await client.get(f"/processes/{dfr['id']}/io-card")).json()
    assert card["trigger"]["kind"] == "jms"

    xml = await client.get(f"/processes/{dfr['id']}/xml")
    assert xml.status_code == 200
    assert "bpws:process" in xml.text

    assert (await client.get("/processes/nope/canvas")).status_code == 404


# ------------------------------------------------------------------ review round 1
async def _upload_zip(client, pid: str, src: Path, tmp_path: Path) -> str:
    archive = shutil.make_archive(str(tmp_path / f"{src.name}-{pid}"), "zip", root_dir=src.parent,
                                  base_dir=src.name)
    r = await client.post(f"/projects/{pid}/upload",
                          files={"file": (f"{src.name}.zip", Path(archive).read_bytes(), "application/zip")})
    assert r.status_code == 201, r.text
    return r.json()["upload_id"]


async def _scan(client, pid: str) -> dict:
    r = await client.post(f"/projects/{pid}/scan", json={})
    assert r.status_code == 202, r.text
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    return job["result"]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


async def test_spec_hash_and_stored_paths_are_upload_independent(client, app, settings, tmp_path):
    a = await _project(client, "by-zip")
    b = await _project(client, "by-path")
    await _upload_zip(client, a, SYNTHETIC, tmp_path)
    r = await client.post(f"/projects/{b}/upload", json={"path": str(SYNTHETIC)})
    assert r.status_code == 201, r.text
    await _scan(client, a)
    await _scan(client, b)
    ha = {p["ir_id"]: p["spec_hash"] for p in (await client.get(f"/projects/{a}/processes")).json()}
    hb = {p["ir_id"]: p["spec_hash"] for p in (await client.get(f"/projects/{b}/processes")).json()}
    assert len(ha) == 11 and ha == hb

    data_dir = str(settings.data_dir.resolve())
    forbidden = (data_dir, str(SYNTHETIC), str(SYNTHETIC.resolve()))
    async with app.state.sessionmaker() as s:
        payloads = [p.ir for p in (await s.execute(select(Process))).scalars()]
        payloads += [x.ir for x in (await s.execute(select(Application))).scalars()]
        payloads += [x.report for x in (await s.execute(select(ScanReport))).scalars()]
        payloads += [x.evidence for x in (await s.execute(select(Domain))).scalars()]
        payloads += [x.attrs for x in (await s.execute(select(GraphNode))).scalars()]
        payloads += [x.attrs for x in (await s.execute(select(GraphEdge))).scalars()]
    leaks = [t for t in _strings(payloads) if any(f in t for f in forbidden)]
    assert leaks == []

    report = (await client.get(f"/projects/{a}/report")).json()
    assert report["explained"] and not report["explained"][0]["path"].startswith("/")
    ir = (await client.get(f"/processes/{(await client.get(f'/projects/{a}/processes')).json()[0]['id']}")).json()
    assert not ir["source_path"].startswith("/")


async def test_server_path_upload_is_restricted(client, settings, tmp_path):
    defaults = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert defaults.allowed_upload_roots == [(tmp_path / "d").resolve(),
                                             (CONFIG_REPO_ROOT / "samples").resolve()]
    pid = await _project(client, "a")
    other = await _project(client, "b")
    r = await client.post(f"/projects/{pid}/upload", json={"path": "/etc"})
    assert r.status_code == 400 and r.json() == {"detail": "path not allowed: /etc"}
    escape = tmp_path / "escape"
    escape.symlink_to("/etc")
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(escape)})
    assert r.status_code == 400 and r.json()["detail"].startswith("path not allowed")
    # another project's upload folder is refused, even though DATA_DIR is an allowed root
    src = tmp_path / "small"
    (src / "P").mkdir(parents=True)
    (src / "P" / "a.process").write_text("<x/>")
    (src / "b.txt").write_text("b")
    r = await client.post(f"/projects/{other}/upload", json={"path": str(src)})
    assert r.status_code == 201, r.text
    theirs = settings.uploads_dir / other / r.json()["upload_id"]
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(theirs)})
    assert r.status_code == 400 and r.json()["detail"] == f"path not allowed: {theirs}"
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(SYNTHETIC)})
    assert r.status_code == 201


async def test_scan_refused_while_one_is_active(client, app):
    pid = await _project(client)
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(SYNTHETIC)})
    assert r.status_code == 201
    async with app.state.sessionmaker() as s:
        s.add(Job(kind="scan", status="running", project_id=pid, payload={}))
        await s.commit()
    r = await client.post(f"/projects/{pid}/scan", json={})
    assert r.status_code == 409
    assert "already in progress" in r.json()["detail"]


async def test_rescan_keeps_process_ids_and_downstream_rows(client, app, tmp_path):
    estate = tmp_path / "estate"
    shutil.copytree(SYNTHETIC, estate)
    pid = await _project(client)
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(estate)})
    assert r.status_code == 201
    first = await _scan(client, pid)
    assert first["added"] == 11 and first["changed"] == 0 and first["removed"] == 0
    before = {p["ir_id"]: p for p in (await client.get(f"/projects/{pid}/processes")).json()}
    dfr = before["dealerfunding.DealerFundingRequest"]
    async with app.state.sessionmaker() as s:
        dom = await s.scalar(select(Domain).where(Domain.project_id == pid, Domain.key == "dealer-funding"))
        dom.name, dom.renamed_by = "Dealer Funding Desk", "sme.vicky"
        s.add(Rule(process_id=dfr["id"], rule_key="R1", text="a rule", citations=[]))
        s.add(Finding(process_id=dfr["id"], finding_key="F1", severity="low", text="f", citations=[],
                      source="static"))
        s.add(Gate(process_id=dfr["id"], gate="G1", state="approved", spec_hash=dfr["spec_hash"]))
        await s.commit()

    # change one process (its source lines move), drop the BW5 application entirely
    bwp = estate / "DealerFunding/Processes/dealerfunding/DealerFundingRequest.bwp"
    text = bwp.read_text()
    head, _, rest = text.partition("\n")
    bwp.write_text(head + "\n\n" + rest)
    shutil.rmtree(estate / "LegacyAddress")
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(estate)})
    assert r.status_code == 201
    second = await _scan(client, pid)
    assert second["processes"] == 10 and second["added"] == 0
    assert second["changed"] == 1 and second["removed"] == 1

    after = {p["ir_id"]: p for p in (await client.get(f"/projects/{pid}/processes")).json()}
    assert "LegacyAddressStandardize" not in after
    assert {k: v["id"] for k, v in after.items()} == {k: before[k]["id"] for k in after}
    new_hash = after["dealerfunding.DealerFundingRequest"]["spec_hash"]
    assert new_hash != dfr["spec_hash"]
    assert after["dealerfunding.DealerFundingRequest"]["domain_name"] == "Dealer Funding Desk"
    unchanged = "creditorigination.AuditLog"
    assert after[unchanged]["spec_hash"] == before[unchanged]["spec_hash"]

    async with app.state.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Rule).where(Rule.process_id == dfr["id"])) == 1
        assert await s.scalar(select(func.count()).select_from(Finding)
                              .where(Finding.process_id == dfr["id"])) == 1
        gate = await s.scalar(select(Gate).where(Gate.process_id == dfr["id"]))
        assert gate.state == "approved" and gate.spec_hash != new_hash  # stale by hash
        actions = [a.action for a in (await s.execute(
            select(AuditLog).where(AuditLog.project_id == pid).order_by(AuditLog.id))).scalars()]
        assert f"rescanned: spec hash changed {dfr['spec_hash']} -> {new_hash}" in actions
        assert "rescanned: process removed LegacyAddressStandardize" in actions
        assert len(actions) == 2
        apps = (await s.execute(select(Application.app_key).where(Application.project_id == pid))).scalars()
        assert sorted(apps) == ["gmf.credit"]
        domains = (await s.execute(select(Domain.key).where(Domain.project_id == pid))).scalars().all()
        assert "legacy-address" not in domains and len(domains) == 3
