"""GitHub connector (Task 3): connection test, branches, clone, push a build, pull requests.

Against ``fake_github`` (REST and git smart HTTP on 127.0.0.1). The push job runs in the app's own
worker; the admin layer is switched on by writing the stored setting directly."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from api.app.connectors.base import ConnectorError, HostNotAllowed
from api.app.connectors.config import STORED_KEY
from api.app.connectors.gitcli import auth_header_value, git_env, run_git
from api.app.connectors.github import GitHubConnector, default_push_branch, valid_push_branch
from api.app.connectors.registry import get_connector
from api.app.db import make_engine, reset_schema
from api.app.main import create_app
from api.app.models import Application, AuditLog, Build, JobEvent, Process, Project, Setting, Upload
from api.app.services.build_tree import build_tree_files

ENGINEER = {"X-User": "engineer.ravi"}
VIEWER = {"X-User": "viewer.val"}
ON = {STORED_KEY: {"github": {"enabled": True}}}


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def gh_settings(connectors_settings, fake_github):
    return connectors_settings(github=fake_github)


@pytest.fixture
def gh(gh_settings) -> GitHubConnector:
    """The connector bound to the fake, both layers on."""
    connector = get_connector("github", settings=gh_settings, stored=ON)
    assert isinstance(connector, GitHubConnector)
    return connector


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


@pytest.fixture
def engineer() -> dict[str, str]:
    return ENGINEER


async def _seed_build(app, root: Path) -> tuple[str, str]:
    """A process and a finished build whose solution is ``root`` (with folders that never ship)."""
    files = {"GmfCredit.sln": "sln", "src/Credit/Program.cs": "class P {}", "run-local.sh": "echo hi\n",
             "bin/Debug/app.dll": "dll", "src/Credit/obj/x.json": "{}", ".exiqo/state.json": "{}",
             "TestResults/r.trx": "<x/>"}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    async with app.state.sessionmaker() as s:
        project = Project(name="credit")
        s.add(project)
        await s.flush()
        upload = Upload(project_id=project.id, source_kind="path", source_name="x", root_dir="/tmp")
        s.add(upload)
        await s.flush()
        application = Application(project_id=project.id, upload_id=upload.id, app_key="a", name="a",
                                  dialect="bw5", ir={})
        s.add(application)
        await s.flush()
        proc = Process(project_id=project.id, application_id=application.id, upload_id=upload.id,
                       ir_id="AccountLookup", name="Account Lookup", dialect="bw5",
                       source_path="loanservicing/AccountLookup.process", ir={}, spec_hash="s" * 64)
        s.add(proc)
        await s.flush()
        build = Build(process_id=proc.id, status="passed", output_dir=str(root), build_hash="b" * 64)
        s.add(build)
        await s.commit()
        return proc.id, build.id


@pytest.fixture
async def seeded(app, tmp_path) -> tuple[str, str]:
    root = tmp_path / "build-out"
    root.mkdir()
    return await _seed_build(app, root)


@pytest.fixture
def finished_build(seeded) -> str:
    return seeded[1]


@pytest.fixture
def proc_id(seeded) -> str:
    return seeded[0]


async def _wait(client: httpx.AsyncClient, job_id: str, timeout: float = 60.0) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            job = (await client.get(f"/jobs/{job_id}")).json()
            if job["status"] in ("done", "failed"):
                return job
            await asyncio.sleep(0.05)


@pytest.fixture
def wait_terminal():
    async def wait(client: httpx.AsyncClient, job_id: str) -> dict:
        job = await _wait(client, job_id)
        assert job["status"] == "done", job["error"]
        return job["result"]

    return wait


@pytest.fixture
async def pushed_once(client, fake_github, finished_build, engineer, wait_terminal) -> str:
    fake_github.add_repo("gmf/credit", tree={"README.md": "x"})
    r = await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"}, headers=engineer)
    assert r.status_code == 202, r.text
    out = await wait_terminal(client, r.json()["job_id"])
    assert out["pull_request"]["number"] == 1
    return finished_build


async def job_log_text(app) -> str:
    async with app.state.sessionmaker() as s:
        return json.dumps([[e.text, e.data] for e in (await s.execute(select(JobEvent))).scalars()], default=str)


async def audit_text(app) -> str:
    async with app.state.sessionmaker() as s:
        return json.dumps([[a.action, a.detail] for a in (await s.execute(select(AuditLog))).scalars()],
                          default=str)


async def _audit_rows(app) -> list[AuditLog]:
    async with app.state.sessionmaker() as s:
        return list((await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars())


# ------------------------------------------------------------------ brief tests


async def test_test_connection_reports_login(gh):
    result = await gh.test()
    assert result.ok and result.account == "exiqo-bot"
    assert "4999" in result.message  # the rate limit is read too


async def test_push_build_and_open_pr(client, app, fake_github, finished_build, proc_id, engineer, wait_terminal):
    fake_github.add_repo("gmf/credit", tree={"README.md": "x"})
    job = (await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"},
                             headers=engineer)).json()
    out = await wait_terminal(client, job["job_id"])
    files = fake_github.branch_files("gmf/credit", out["branch"])
    assert out["branch"] == f"exiqo/account-lookup/build-{finished_build[:8]}"
    assert "EXPORTED_FROM.md" in files and "README.md" in files  # on top of the base branch
    assert finished_build in files["EXPORTED_FROM.md"]
    # the download's exclusions apply to the push (X3)
    assert set(files) == {"README.md", "EXPORTED_FROM.md", "GmfCredit.sln", "src/Credit/Program.cs", "run-local.sh"}
    assert out["pull_request"]["number"] == 1 and out["pull_request"]["url"].endswith("/gmf/credit/pull/1")
    assert len(out["commit"]) == 40
    pull = fake_github.pulls["gmf/credit"][0]
    assert pull["head"]["ref"] == out["branch"] and pull["base"]["ref"] == "main"
    links = (await client.get(f"/processes/{proc_id}/links")).json()
    assert {lk["object_type"] for lk in links} >= {"branch", "pull_request"}
    assert all(lk["local_ref"] == finished_build for lk in links)
    actions = {(a.action, a.detail["outcome"]) for a in await _audit_rows(app)}
    assert {("connector github push", "ok"), ("connector github pull-request", "ok")} <= actions


async def test_second_push_reuses_the_open_pr(client, app, fake_github, pushed_once, engineer, wait_terminal):
    out = await wait_terminal(client, (await client.post(f"/builds/{pushed_once}/github/push",
                                                         json={"repo": "gmf/credit"}, headers=engineer)).json()["job_id"])
    assert out["pull_request"]["number"] == 1
    assert len(fake_github.pulls["gmf/credit"]) == 1
    patched = [r for r in fake_github.requests if r.method == "PATCH" and r.path.endswith("/pulls/1")]
    assert patched and out["commit"] in patched[-1].json["body"]
    async with app.state.sessionmaker() as s:
        from api.app.models.connector import ConnectorLink

        assert len((await s.execute(select(ConnectorLink))).scalars().all()) == 2  # upserted, not added


async def test_push_refuses_non_exiqo_branch(gh, fake_github, tmp_path):
    with pytest.raises(ValueError, match="exiqo/ branches only"):
        await gh.push_build(tmp_path, "gmf/credit", "main", "m")
    for bad in ("exiqo/", "exiqo/../main", "-exiqo/x", "exiqo/a b", "exiqo/x.lock"):
        assert not valid_push_branch(bad), bad
    assert fake_github.requests == []


async def test_push_never_copies_git_paths_or_symlinks(gh, fake_github, tmp_path):  # Review Focus 5
    root = tmp_path / "b"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("[credential]\nhelper=!evil")
    (root / "sub" / ".GIT").mkdir(parents=True)
    (root / "sub" / ".GIT" / "hooks").write_text("evil")
    (root / "ok.txt").write_text("1")
    (root / "link").symlink_to("/etc/passwd")
    (root / "dirlink").symlink_to(tmp_path)
    fake_github.add_repo("gmf/x")
    before = set(os.listdir(tmp_path))
    await gh.push_build(root, "gmf/x", "exiqo/t", "m")
    assert set(fake_github.branch_files("gmf/x", "exiqo/t")) == {"ok.txt"}
    assert set(os.listdir(tmp_path)) == before  # nothing written outside the temp repository


async def test_token_never_in_job_log_or_audit(client, app, fake_github, finished_build, engineer, wait_terminal,
                                               token):
    fake_github.add_repo("gmf/credit")
    job_id = (await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"},
                                headers=engineer)).json()["job_id"]
    out = await wait_terminal(client, job_id)
    job = (await client.get(f"/jobs/{job_id}")).text
    assert token not in json.dumps(out) and token not in job
    assert token not in await job_log_text(app) and token not in await audit_text(app)


async def test_failed_push_is_audited_and_redacted(client, app, fake_github, finished_build, engineer, token):
    """No such repository: the job fails with GitHub's answer, the token in no error or audit."""
    job_id = (await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/absent"},
                                headers=engineer)).json()["job_id"]
    job = await _wait(client, job_id)
    assert job["status"] == "failed" and "404" in job["error"] and token not in job["error"]
    rows = [(a.action, a.detail["outcome"]) for a in await _audit_rows(app)]
    assert ("connector github push", "failed") in rows and token not in await audit_text(app)


async def test_disabled_makes_no_request(client_github_disabled, fake_github, tmp_path, engineer):
    root = tmp_path / "out"
    root.mkdir()
    _, build_id = await _seed_build(client_github_disabled.app, root)
    r = await client_github_disabled.post(f"/builds/{build_id}/github/push", json={"repo": "gmf/credit"},
                                          headers=engineer)
    assert r.status_code == 409 and fake_github.requests == []
    rows = await _audit_rows(client_github_disabled.app)
    assert [(a.action, a.detail["outcome"]) for a in rows] == [("connector github push", "refused")]


async def test_clone_and_head(gh, fake_github, tmp_path):
    fake_github.add_repo("gmf/estate", tree={"a.process": "<x/>"})
    sha = await gh.clone("gmf/estate", "main", tmp_path / "c")
    assert sha == await gh.head("gmf/estate", "main") and (tmp_path / "c" / "a.process").is_file()
    assert "Authorization" not in (tmp_path / "c" / ".git" / "config").read_text()


# ------------------------------------------------------------------ more


async def test_route_roles_and_validation(client, finished_build):
    r = await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"}, headers=VIEWER)
    assert r.status_code == 403 and r.json()["detail"] == "viewer is read only"
    r = await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"},
                          headers={"X-User": "architect.lee"})
    assert r.status_code == 403 and "engineer" in r.json()["detail"]
    r = await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit", "branch": "main"},
                          headers=ENGINEER)
    assert r.status_code == 422 and r.json()["detail"] == "pushes go to exiqo/ branches only"
    r = await client.post(f"/builds/{finished_build}/github/push", json={"repo": "../etc"}, headers=ENGINEER)
    assert r.status_code == 422
    r = await client.post("/builds/nope/github/push", json={"repo": "gmf/credit"}, headers=ENGINEER)
    assert r.status_code == 404


async def test_missing_credential_is_409(client, finished_build, fake_github, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tmp_path / "missing"))
    r = await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"}, headers=ENGINEER)
    assert r.status_code == 409 and "GITHUB_TOKEN_FILE" in r.json()["detail"]
    assert fake_github.requests == []


async def test_list_branches_follows_link_pages(gh, fake_github):
    fake_github.add_repo("gmf/many")
    fake_github.commit("exiqo/a", {"a": "1"}, repo="gmf/many")
    names = await gh.list_branches("gmf/many")
    assert set(names) == {"main", "exiqo/a"}
    first = next(r for r in fake_github.requests if r.path.endswith("/branches"))
    assert "per_page=100" in first.query


async def test_list_branches_paginates_and_caps(gh, monkeypatch):
    """Three pages by ``Link: rel=next``; at most 1 000 names."""
    calls: list[str] = []

    async def fake_call(method, url, **kw):
        calls.append(url)
        page = len(calls)
        headers = {"link": f'<{gh.api_url}/repos/gmf/r/branches?per_page=100&page={page + 1}>; rel="next"'}
        return httpx.Response(200, json=[{"name": f"b{page}-{i}"} for i in range(400)], headers=headers)

    monkeypatch.setattr(gh, "_call", fake_call)
    names = await gh.list_branches("gmf/r")
    assert len(names) == 1000 and len(calls) == 3
    assert calls[1].startswith(gh.api_url) and "page=2" in calls[1]


async def test_open_pr_after_a_422_already_exists_updates_that_one(gh, fake_github, monkeypatch):
    """Another push opened the PR between our lookup and our POST: GitHub answers 422."""
    fake_github.add_repo("gmf/race")
    fake_github.commit("exiqo/r", {"a": "1"}, repo="gmf/race")
    first = await gh.open_pull_request("gmf/race", "exiqo/r", "main", "t", "one")
    real = gh._open_pull_for
    calls = {"n": 0}

    async def miss_once(repo, head):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(repo, head)

    monkeypatch.setattr(gh, "_open_pull_for", miss_once)
    again = await gh.open_pull_request("gmf/race", "exiqo/r", "main", "t", "two")
    assert again["number"] == first["number"] and len(fake_github.pulls["gmf/race"]) == 1
    assert fake_github.pulls["gmf/race"][0]["body"] == "two" and calls["n"] == 2


async def test_remote_off_the_allowlist_is_refused(gh_settings, fake_github, tmp_path):
    other = gh_settings.model_copy(update={"connectors": json.dumps({"github": {
        "enabled": True, "baseUrl": fake_github.url, "allowedHosts": ["github.gmf.example.com"]}})})
    connector = get_connector("github", settings=other, stored=ON)
    with pytest.raises(HostNotAllowed):
        await connector.clone("gmf/estate", "main", tmp_path / "c")
    assert fake_github.requests == []


def test_git_env_is_hermetic_and_scoped(monkeypatch):
    monkeypatch.setenv("GIT_ASKPASS", "/usr/bin/evil")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'credential.helper'='!evil'")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xyz")
    env = git_env("tok-123", allow_http_loopback=False, remote_root="https://github.gmf.example.com",
                  ca_bundle="/etc/gmf-ca.pem")
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_ASKPASS"] == ""
    assert env["GIT_SSL_CAINFO"] == "/etc/gmf-ca.pem" and env["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert "GIT_CONFIG_PARAMETERS" not in env and "OPENAI_API_KEY" not in env
    assert not Path(env["HOME"]).exists()
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs["http.https://github.gmf.example.com/.extraHeader"] == "Authorization: " + auth_header_value("tok-123")
    assert pairs["protocol.allow"] == "never" and pairs["protocol.https.allow"] == "always"
    assert "protocol.http.allow" not in pairs and pairs["core.hooksPath"] == os.devnull
    assert all("tok-123" not in v for k, v in env.items() if not k.startswith("GIT_CONFIG_VALUE_"))
    loop = git_env("t", allow_http_loopback=True, remote_root="http://127.0.0.1:1")
    assert "protocol.http.allow" in {loop[f"GIT_CONFIG_KEY_{i}"] for i in range(int(loop["GIT_CONFIG_COUNT"]))}


def test_run_git_redacts_and_times_out(tmp_path, fake_github):
    env = git_env(fake_github.token, allow_http_loopback=False, remote_root=fake_github.url)
    with pytest.raises(ConnectorError, match="transport 'http' not allowed"):  # without the loopback flag
        run_git(["ls-remote", f"{fake_github.url}/gmf/x.git"], tmp_path, env, 30)
    env = git_env(fake_github.token, allow_http_loopback=True, remote_root=fake_github.url)
    with pytest.raises(ConnectorError) as err:  # git names the URL, which here holds the token
        run_git(["ls-remote", f"{fake_github.url}/gmf/{fake_github.token}.git"], tmp_path, env, 30)
    assert fake_github.token not in err.value.message and "***" in err.value.message
    with pytest.raises(ConnectorError, match="timed out after 1 s"):
        run_git(["-c", "alias.slow=!sleep 5", "slow"], tmp_path, env, 1)


def test_build_tree_files_is_the_one_answer(tmp_path):
    (tmp_path / "a" / "bin").mkdir(parents=True)
    (tmp_path / "a" / "bin" / "x").write_text("1")
    (tmp_path / "a" / "k.cs").write_text("1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("1")
    (tmp_path / "l").symlink_to(tmp_path / "a" / "k.cs")
    assert build_tree_files(tmp_path) == ["a/k.cs"]


def test_default_branch_name():
    assert default_push_branch("Account Lookup v2", "0123456789ab") == "exiqo/account-lookup-v2/build-01234567"
