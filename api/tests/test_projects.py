"""Projects, uploads, scan and the process read views (Task 17)."""
from __future__ import annotations

import asyncio
import io
import shutil
import zipfile
from pathlib import Path

from sqlalchemy import func, select

from api.app.models import Domain, GraphEdge, GraphNode

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


async def test_upload_refuses_unsafe_zip(client, settings):
    pid = await _project(client)
    for bad in ("../evil.txt", "/abs.txt"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ok/readme.txt", "fine")
            zf.writestr(bad, "nope")
        r = await client.post(f"/projects/{pid}/upload",
                              files={"file": ("estate.zip", buf.getvalue(), "application/zip")})
        assert r.status_code == 400, r.text
        assert r.json() == {"detail": f"unsafe path: {bad}"}
    # nothing escaped the uploads folder and nothing was kept from the refused uploads
    assert not (settings.data_dir / "evil.txt").exists()
    assert not (settings.data_dir / "uploads" / "evil.txt").exists()
    assert not Path("/abs.txt").exists()
    project_dir = settings.data_dir / "uploads" / pid
    assert not project_dir.exists() or not any(project_dir.rglob("readme.txt"))


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
