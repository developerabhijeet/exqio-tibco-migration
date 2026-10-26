"""A GitHub repository as a source (sub-project 6, Task 9): connect a branch, fetch it, and classify
it: an exported build (``EXIQO_BASELINE.json``) becomes an edit set, anything else an upload and a
scan. Against ``fake_github`` (REST and git smart HTTP on 127.0.0.1); the jobs run in the app's
worker."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from api.app.connectors.config import STORED_KEY
from api.app.db import make_engine, reset_schema
from api.app.main import create_app
from api.app.models import (
    Application,
    AuditLog,
    Build,
    BuildFile,
    Job,
    Process,
    Project,
    ProjectRepo,
    Setting,
    Upload,
)
from api.app.services.build_tree import DOWNLOAD_EXCLUDED, build_tree_files
from api.app.services.edits import BASELINE_NAME, baseline_manifest, text_sha256
from api.tests.conftest import REPO_ROOT

SYNTHETIC_GMF = REPO_ROOT / "samples" / "synthetic-gmf"
ON = {STORED_KEY: {"github": {"enabled": True}}}
P = "proj-repos-test"
PROC = "proc-repos-test"
AGENT_FILE = "src/Credit/Mappings.cs"
FILES = {"GmfCredit.sln": "sln\n", "src/Credit/Program.cs": "class P {}\n",
         AGENT_FILE: "class M {\n    int Map() => 1; // agent fills\n}\n", "infra/main.tf": "# tf\n"}
ORIGINS = {"GmfCredit.sln": "code", "src/Credit/Program.cs": "code", AGENT_FILE: "agent", "infra/main.tf": "infra"}


def replace_body(old: str) -> str:
    return old.replace("=> 1; // agent fills", "=> 2; // fixed by hand")


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def gh_settings(connectors_settings, fake_github):
    return connectors_settings(github=fake_github)


@pytest.fixture
async def app(gh_settings):
    """The app with GitHub on in the install (the fake) and in Settings."""
    engine = make_engine(gh_settings.database_url)
    try:
        await reset_schema(engine)
    except Exception as exc:  # noqa: BLE001  no database: skip, do not fail the suite
        await engine.dispose()
        pytest.skip(f"Postgres is not available: {exc}")
    await engine.dispose()
    application = create_app(gh_settings)
    async with application.router.lifespan_context(application):
        async with application.state.sessionmaker() as s:
            s.add(Setting(key=STORED_KEY, value=ON[STORED_KEY], updated_by="ops.sam"))
            await s.commit()
        yield application


async def seed_project(app) -> None:
    async with app.state.sessionmaker() as s:
        s.add(Project(id=P, name="repos"))
        await s.commit()


@pytest.fixture
async def project(app) -> str:
    await seed_project(app)
    return P


@pytest.fixture
async def finished_build(app, project, tmp_path) -> str:
    """A process ``PROC`` and its finished build, whose solution folder holds ``FILES``."""
    root = tmp_path / "build-out"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    async with app.state.sessionmaker() as s:
        upload = Upload(project_id=P, source_kind="path", source_name="x", root_dir="/tmp")
        s.add(upload)
        await s.flush()
        application = Application(project_id=P, upload_id=upload.id, app_key="a", name="a", dialect="bw5", ir={})
        s.add(application)
        await s.flush()
        s.add(Process(id=PROC, project_id=P, application_id=application.id, upload_id=upload.id,
                      ir_id="dealerfunding.DealerFundingRequest", name="Dealer Funding Request", dialect="bw5",
                      source_path="DealerFunding/DealerFundingRequest.process", ir={}, spec_hash="s" * 64))
        await s.flush()
        build = Build(process_id=PROC, status="passed", output_dir=str(root), build_hash="b" * 64,
                      design_hash="d" * 64)
        s.add(build)
        await s.flush()
        s.add_all(BuildFile(build_id=build.id, path=rel, origin=ORIGINS[rel], sha256=text_sha256(text.encode()),
                            size=len(text)) for rel, text in FILES.items())
        await s.commit()
        return build.id


async def edited_export_of(app, build_id: str, changes: dict) -> dict[str, str]:
    """The build as its download ships it (manifest included), with ``changes`` applied."""
    async with app.state.sessionmaker() as s:
        build = await s.get(Build, build_id)
        proc = await s.get(Process, build.process_id)
    root = Path(build.output_dir)
    files = {rel: (root / rel).read_text(encoding="utf-8") for rel in build_tree_files(root, DOWNLOAD_EXCLUDED)}
    manifest = baseline_manifest(build, proc, {rel: text_sha256(t.encode()) for rel, t in files.items()})
    for rel, change in changes.items():
        files[rel] = change(files.get(rel, "")) if callable(change) else change
    files[BASELINE_NAME] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    return files


@pytest.fixture
def edited_export(app):
    async def make(build_id: str, changes: dict) -> dict[str, str]:
        return await edited_export_of(app, build_id, changes)

    return make


@pytest.fixture
def engineer() -> dict[str, str]:
    return {"X-User": "engineer.ravi"}


@pytest.fixture
def sme() -> dict[str, str]:
    return {"X-User": "sme.vicky"}


async def wait_job(client: httpx.AsyncClient, job_id: str, timeout: float = 120.0) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            job = (await client.get(f"/jobs/{job_id}")).json()
            if job["status"] in ("done", "failed"):
                return job
            await asyncio.sleep(0.05)


async def wait_terminal(client: httpx.AsyncClient, job_id: str) -> dict:
    job = await wait_job(client, job_id)
    assert job["status"] == "done", job["error"]
    return job["result"]


async def latest_upload(client, project_id: str) -> dict:
    async with client._transport.app.state.sessionmaker() as s:
        row = await s.scalar(select(Upload).where(Upload.project_id == project_id, Upload.source_kind == "repo")
                             .order_by(Upload.created_at.desc()).limit(1))
    return {"id": row.id, "source_name": row.source_name, "root_dir": row.root_dir, "scan_root": row.scan_root}


async def latest_job(client, project_id: str, kind: str) -> Job | None:
    async with client._transport.app.state.sessionmaker() as s:
        return await s.scalar(select(Job).where(Job.project_id == project_id, Job.kind == kind)
                              .order_by(Job.created_at.desc()).limit(1))


@pytest.fixture
async def connected_build_repo(client, fake_github, finished_build, engineer) -> str:
    """``gmf/svc`` connected for ``PROC`` on its empty ``main`` (fetched: nothing to import)."""
    fake_github.add_repo("gmf/svc", branch="main")
    r = await client.post(f"/projects/{P}/repos", headers=engineer,
                          json={"repo": "gmf/svc", "branch": "main", "purpose": "build", "process_id": PROC})
    assert r.status_code == 201, r.text
    out = await wait_terminal(client, r.json()["job_id"])
    assert out["kind"] == "none"
    return r.json()["id"]


# ------------------------------------------------------------------ the brief's tests
async def test_estate_branch_creates_upload_and_scan(client, fake_github, project, sme):
    fake_github.add_repo("gmf/estate", branch="main", tree=SYNTHETIC_GMF)
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/estate", "branch": "main", "purpose": "estate"}, headers=sme)
    out = await wait_terminal(client, r.json()["job_id"])
    assert out["kind"] == "upload" and (await latest_upload(client, P))["source_name"].startswith("gmf/estate@main:")
    assert await latest_job(client, P, "scan")


async def test_build_branch_creates_edit_set(client, fake_github, finished_build, engineer, edited_export):  # spec criterion 3
    fake_github.add_repo("gmf/svc", branch="fix", tree=await edited_export(finished_build, {AGENT_FILE: replace_body}))
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/svc", "branch": "fix", "purpose": "build", "process_id": PROC}, headers=engineer)
    out = await wait_terminal(client, r.json()["job_id"])
    es = (await client.get(f"/edit-sets/{out['id']}")).json()
    it = (await client.get(f"/iterations/{es['iteration_id']}")).json()
    assert [f["path"] for f in es["files"]] == [AGENT_FILE] and it["kind"] == "repo"
    assert it["source"]["repo_id"] == r.json()["id"] and it["source"]["branch"] == "fix"
    assert it["source"]["commit"] == out["commit"] and es["baseline_build_id"] == finished_build


async def test_pull_without_new_commit_changes_nothing(client, connected_build_repo, engineer):
    assert (await client.post(f"/repos/{connected_build_repo}/pull", headers=engineer)).json() == {"changed": False}


async def test_disabled_connector_is_409(github_disabled, engineer):
    client = github_disabled
    await seed_project(client.app)
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/x", "branch": "main", "purpose": "estate"}, headers=engineer)
    assert r.status_code == 409 and r.json()["detail"] == "the GitHub connector is not enabled"


async def test_bad_repo_name_is_422(client, fake_github, project, engineer):
    r = await client.post(f"/projects/{P}/repos", json={"repo": "../../etc", "branch": "main", "purpose": "estate"}, headers=engineer)
    assert r.status_code == 422


# ------------------------------------------------------------------ more
async def test_pull_with_a_new_commit_fetches_it(client, fake_github, connected_build_repo, finished_build, engineer,
                                                 edited_export):
    sha = fake_github.commit("main", await edited_export(finished_build, {AGENT_FILE: replace_body}), repo="gmf/svc")
    r = await client.post(f"/repos/{connected_build_repo}/pull", headers=engineer)
    assert r.status_code == 202 and r.json()["changed"] is True
    out = await wait_terminal(client, r.json()["job_id"])
    assert out["kind"] == "edit_set" and out["commit"] == sha
    repos = (await client.get(f"/projects/{P}/repos")).json()
    assert repos[0]["last_commit"] == sha and repos[0]["repo"] == "gmf/svc" and repos[0]["branch"] == "main"
    assert (await client.post(f"/repos/{connected_build_repo}/pull", headers=engineer)).json() == {"changed": False}
    async with client._transport.app.state.sessionmaker() as s:
        actions = [a.action for a in (await s.scalars(select(AuditLog).order_by(AuditLog.id))).all()]
    assert "repo connected" in actions and actions.count("repo pulled") == 2


async def test_branches_are_listed(client, fake_github, connected_build_repo):
    fake_github.commit("feature/x", {"a.txt": "a"}, repo="gmf/svc")
    branches = (await client.get(f"/repos/{connected_build_repo}/branches")).json()["branches"]
    assert set(branches) == {"main", "feature/x"}


async def test_sme_connects_estates_only(client, fake_github, finished_build, sme):
    r = await client.post(f"/projects/{P}/repos", headers=sme,
                          json={"repo": "gmf/svc", "branch": "main", "purpose": "build", "process_id": PROC})
    assert r.status_code == 403 and r.json()["detail"] == "an SME may connect an estate repository only"
    r = await client.post(f"/projects/{P}/repos", headers={"X-User": "qa.priya"},
                          json={"repo": "gmf/estate", "branch": "main", "purpose": "estate"})
    assert r.status_code == 403


@pytest.mark.parametrize("branch", ["-x", "a..b", "a b", "refs/heads/../x", "x.lock", ""])
async def test_bad_branch_is_422(client, fake_github, project, engineer, branch):
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/estate", "branch": branch, "purpose": "estate"},
                          headers=engineer)
    assert r.status_code == 422


async def test_build_purpose_needs_a_process_of_the_project(client, fake_github, project, engineer):
    fake_github.add_repo("gmf/svc")
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/svc", "branch": "main", "purpose": "build"},
                          headers=engineer)
    assert r.status_code == 422
    r = await client.post(f"/projects/{P}/repos", headers=engineer,
                          json={"repo": "gmf/svc", "branch": "main", "purpose": "build", "process_id": "nope"})
    assert r.status_code == 404


async def test_unknown_branch_is_502_with_the_connector_message(client, fake_github, project, engineer):
    fake_github.add_repo("gmf/estate")
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/estate", "branch": "nope", "purpose": "estate"},
                          headers=engineer)
    assert r.status_code == 502 and "GitHub" in r.json()["detail"]
    async with client._transport.app.state.sessionmaker() as s:
        assert (await s.scalar(select(ProjectRepo.id).limit(1))) is None


async def test_a_clone_over_the_size_limit_is_refused(client, app, fake_github, project, engineer):
    fake_github.add_repo("gmf/estate", tree={"big.txt": "x" * 4096})
    app.state.settings.upload_max_bytes = 1024
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/estate", "branch": "main", "purpose": "estate"},
                          headers=engineer)
    job = await wait_job(client, r.json()["job_id"])
    assert job["status"] == "failed" and "larger than" in job["error"]
    repo_dir = app.state.settings.data_dir / "repos" / r.json()["id"]
    assert not any(p for p in repo_dir.rglob("*") if p.is_file()) if repo_dir.exists() else True


async def test_estate_upload_has_no_git_folder(client, fake_github, project, engineer):
    fake_github.add_repo("gmf/estate", tree={"a/x.process": "<x/>", "a/b.txt": "b"})
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/estate", "branch": "main", "purpose": "estate"},
                          headers=engineer)
    await wait_terminal(client, r.json()["job_id"])
    up = await latest_upload(client, P)
    assert not (Path(up["root_dir"]) / ".git").exists() and (Path(up["root_dir"]) / "a" / "b.txt").is_file()


# ------------------------------------------------------------------ final review fixes
async def test_an_estate_repository_with_a_manifest_is_still_an_upload(client, fake_github, finished_build, sme,
                                                                       edited_export):  # final I2
    fake_github.add_repo("gmf/estate", tree=await edited_export(finished_build, {AGENT_FILE: replace_body}))
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/estate", "branch": "main", "purpose": "estate"},
                          headers=sme)
    out = await wait_terminal(client, r.json()["job_id"])
    assert out["kind"] == "upload"
    assert [i["kind"] for i in (await client.get(f"/projects/{P}/iterations")).json()] == ["initial"]


async def test_a_manifest_naming_another_process_is_refused(client, app, fake_github, finished_build, engineer,
                                                            edited_export):  # final I2
    async with app.state.sessionmaker() as s:
        proc = await s.get(Process, PROC)
        other = Process(project_id=P, application_id=proc.application_id, upload_id=proc.upload_id, ir_id="x.Other",
                        name="Other", dialect="bw5", source_path="o.process", ir={}, spec_hash="o" * 64)
        s.add(other)
        await s.commit()
        other_id = other.id
    fake_github.add_repo("gmf/svc", tree=await edited_export(finished_build, {AGENT_FILE: replace_body}))
    r = await client.post(f"/projects/{P}/repos", headers=engineer,
                          json={"repo": "gmf/svc", "branch": "main", "purpose": "build", "process_id": other_id})
    job = await wait_job(client, r.json()["job_id"])
    assert job["status"] == "failed" and "names a build of another process" in job["error"]


async def test_a_pull_without_changes_opens_no_iteration(client, fake_github, finished_build, engineer,
                                                         edited_export):  # final I1
    fake_github.add_repo("gmf/svc", tree=await edited_export(finished_build, {}))
    r = await client.post(f"/projects/{P}/repos", headers=engineer,
                          json={"repo": "gmf/svc", "branch": "main", "purpose": "build", "process_id": PROC})
    out = await wait_terminal(client, r.json()["job_id"])
    assert out["kind"] == "none" and "no changes" in out["message"]
    assert [i["kind"] for i in (await client.get(f"/projects/{P}/iterations")).json()] == ["initial"]


async def test_a_refetch_never_touches_an_edit_set_folder(client, app, fake_github, finished_build, engineer,
                                                          edited_export):  # final I5
    from api.app.models import EditSet
    from api.app.services import repos as repos_svc

    files = await edited_export(finished_build, {AGENT_FILE: replace_body})
    fake_github.add_repo("gmf/svc", tree=files)
    r = await client.post(f"/projects/{P}/repos", headers=engineer,
                          json={"repo": "gmf/svc", "branch": "main", "purpose": "build", "process_id": PROC})
    repo_id = r.json()["id"]
    first = await wait_terminal(client, r.json()["job_id"])
    async with app.state.sessionmaker() as s:
        es = await s.get(EditSet, first["id"])
        root = Path(es.root_dir)
        build = Build(process_id=PROC, status="queued", edit_set_id=es.id)  # its build is queued
        s.add(build)
        await s.flush()
        s.add(Job(kind="build", status="queued", project_id=P, process_id=PROC, payload={"build_id": build.id},
                  not_before=datetime.now(UTC) + timedelta(hours=1)))  # held: no worker claims it
        await s.commit()
    fake_github.commit("fix", {AGENT_FILE: lambda t: t + "// more\n"}, repo="gmf/svc")
    other = await client.post(f"/repos/{repo_id}/pull", json={"branch": "fix"}, headers=engineer)
    assert other.status_code == 202
    assert (await wait_job(client, other.json()["job_id"]))["status"] == "failed"  # busy: the build is queued
    # main again: unchanged for main (the other branch's fetch did not move main's last commit)
    assert (await client.post(f"/repos/{repo_id}/pull", json={"branch": "main"}, headers=engineer)).json() == \
        {"changed": False}
    async with app.state.sessionmaker() as s:
        row = await s.get(ProjectRepo, repo_id)
    again = await repos_svc.queue_fetch(app.state.sessionmaker, row, "main", "engineer.ravi")  # forced re-fetch of A
    assert (await wait_job(client, again))["status"] == "failed"
    assert root.is_dir() and (root / AGENT_FILE).read_text().endswith("// fixed by hand\n}\n")
