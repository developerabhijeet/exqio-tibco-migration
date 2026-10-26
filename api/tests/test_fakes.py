"""The local fakes behave like the services the connectors talk to (no real network)."""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from api.tests.fakes.github import COMMIT_FLAGS, hermetic_git_env


def run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=hermetic_git_env(),
                          check=False, timeout=60)


def init_repo(path: Path, files: dict[str, str]) -> Path:
    path = path / "work"
    path.mkdir()
    run_git("init", "-b", "main", cwd=path)
    for name, text in files.items():
        (path / name).write_text(text)
    run_git("add", "-A", cwd=path)
    assert run_git(*COMMIT_FLAGS, "commit", "-m", "init", cwd=path).returncode == 0
    return path


def push(repo: Path, url: str, branch: str, token: str) -> subprocess.CompletedProcess:
    return run_git("-c", f"http.extraHeader=Authorization: Bearer {token}", "push", url,
                   f"HEAD:refs/heads/{branch}", cwd=repo)


async def get_json(fake, path: str, headers: dict[str, str] | None = None):
    async with httpx.AsyncClient() as c:
        r = await c.get(fake.url + path, headers=headers or {"X-Figma-Token": fake.token})
        assert r.status_code == 200, r.text
        return r.json()


def bearer(fake) -> dict[str, str]:
    return {"Authorization": f"Bearer {fake.token}"}


def basic(fake) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{fake.user}:{fake.token}".encode()).decode()}


async def put_page(fake, page_id: str, version: int, headers: dict[str, str] | None = None):
    async with httpx.AsyncClient() as c:
        return await c.put(f"{fake.url}/rest/api/content/{page_id}", headers=headers or bearer(fake),
                           json={"version": {"number": version}, "title": "T", "type": "page",
                                 "body": {"storage": {"value": "<p>x</p>", "representation": "storage"}}})


# ------------------------------------------------------------------ GitHub


async def test_fake_github_rejects_missing_token_on_git(fake_github):
    fake_github.add_repo("gmf/svc")
    proc = run_git("ls-remote", f"{fake_github.url}/gmf/svc.git")
    assert proc.returncode != 0
    assert "terminal prompts disabled" in proc.stderr or "Authentication failed" in proc.stderr \
        or "error: 401" in proc.stderr
    assert fake_github.requests and all(r.path.endswith("info/refs") for r in fake_github.requests)


async def test_fake_github_accepts_bearer_git_push(fake_github, tmp_path, token):
    fake_github.add_repo("gmf/svc")
    repo = init_repo(tmp_path, {"a.txt": "1"})
    proc = push(repo, f"{fake_github.url}/gmf/svc.git", "exiqo/x", token)
    assert proc.returncode == 0, proc.stderr
    assert fake_github.branch_files("gmf/svc", "exiqo/x") == {"a.txt": "1"}


async def test_fake_github_git_push_with_a_wrong_token_fails(fake_github, tmp_path):
    fake_github.add_repo("gmf/svc")
    repo = init_repo(tmp_path, {"a.txt": "1"})
    assert push(repo, f"{fake_github.url}/gmf/svc.git", "exiqo/x", "wrong").returncode != 0
    assert "exiqo/x" not in {b for b in fake_github._branch_names("gmf/svc")}


async def test_fake_github_rest(fake_github, token):
    fake_github.add_repo("gmf/svc", tree={"README.md": "hello", "src/a.cs": "x"})
    fake_github.commit("feature", {"src/a.cs": lambda old: old + "y", "b.txt": "b"})
    fake_github.commit("other", {"c.txt": "c"})
    assert fake_github.branch_files("gmf/svc", "main") == {"README.md": "hello", "src/a.cs": "x"}
    assert fake_github.branch_files("gmf/svc", "feature")["src/a.cs"] == "xy"
    h = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=fake_github.url + "/api/v3", headers=h) as c:
        assert (await c.get("/user")).json() == {"login": "exiqo-bot"}
        assert (await c.get("/rate_limit")).status_code == 200
        assert (await c.get("/repos/gmf/svc")).json()["default_branch"] == "main"
        assert (await c.get("/repos/gmf/none")).status_code == 404
        assert {b["name"] for b in (await c.get("/repos/gmf/svc/branches")).json()} == {"main", "feature", "other"}
        assert (await c.get("/repos/gmf/svc/branches/feature")).json()["name"] == "feature"
        assert (await c.get("/repos/gmf/svc/branches/nope")).status_code == 404
        made = await c.post("/repos/gmf/svc/pulls", json={"title": "T", "head": "feature", "base": "main"})
        assert made.status_code == 201
        assert made.json()["number"] == 1
        assert made.json()["html_url"] == f"{fake_github.url}/gmf/svc/pull/1"
        second = await c.post("/repos/gmf/svc/pulls", json={"title": "U", "head": "other", "base": "main"})
        assert second.json()["number"] == 2
        assert (await c.patch("/repos/gmf/svc/pulls/1", json={"state": "closed"})).json()["state"] == "closed"
        assert [p["number"] for p in (await c.get("/repos/gmf/svc/pulls?state=open")).json()] == [2]
        assert (await c.get("/user", headers={"Authorization": "Bearer no"})).status_code == 401


async def test_fake_github_pull_validation_and_filters(fake_github, token):
    fake_github.add_repo("gmf/svc")
    fake_github.commit("feature", {"a": "1"})
    h = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=fake_github.url + "/api/v3", headers=h) as c:
        pr = {"title": "T", "head": "feature", "base": "main"}
        assert (await c.post("/repos/gmf/svc/pulls", json=pr)).status_code == 201
        dup = await c.post("/repos/gmf/svc/pulls", json=pr)
        assert dup.status_code == 422 and dup.json()["message"] == "Validation Failed"
        assert dup.json()["errors"] == [{"message": "A pull request already exists for gmf:feature."}]
        gone = await c.post("/repos/gmf/svc/pulls", json={**pr, "head": "nope"})
        assert gone.status_code == 422 and gone.json()["errors"]
        assert (await c.post("/repos/gmf/svc/pulls", json={**pr, "base": "nope"})).status_code == 422
        assert len((await c.get("/repos/gmf/svc/pulls", params={"head": "gmf:feature"})).json()) == 1
        assert (await c.get("/repos/gmf/svc/pulls", params={"head": "feature"})).json() == []


async def test_fake_github_paginates_with_link_headers(fake_github, token):
    fake_github.add_repo("gmf/svc")
    for i in range(5):
        fake_github.commit(f"b{i}", {"f": str(i)})
    h = {"Authorization": f"Bearer {token}"}
    names: list[str] = []
    async with httpx.AsyncClient(base_url=fake_github.url + "/api/v3", headers=h) as c:
        page, pages = 1, 0
        while True:
            r = await c.get("/repos/gmf/svc/branches", params={"per_page": 2, "page": page})
            names += [b["name"] for b in r.json()]
            pages += 1
            if 'rel="next"' not in r.headers.get("link", ""):
                break
            page += 1
        assert pages == 3 and sorted(names) == ["b0", "b1", "b2", "b3", "b4", "main"]
        everything = await c.get("/repos/gmf/svc/branches")
        assert len(everything.json()) == 6 and "link" not in everything.headers


async def test_fake_github_git_clone_and_fetch_with_auth(fake_github, tmp_path, token):
    fake_github.add_repo("gmf/svc", tree={"a.txt": "1"})
    auth = ["-c", f"http.extraHeader=Authorization: Bearer {token}"]
    url = f"{fake_github.url}/gmf/svc.git"
    clone = tmp_path / "clone"
    assert run_git(*auth, "clone", "--quiet", url, str(clone)).returncode == 0
    assert (clone / "a.txt").read_text() == "1"
    fake_github.commit("main", {"a.txt": "2"})
    assert run_git(*auth, "pull", "--quiet", "--ff-only", cwd=clone).returncode == 0
    assert (clone / "a.txt").read_text() == "2"
    assert run_git("clone", url, str(tmp_path / "anon")).returncode != 0


async def test_fake_github_git_accepts_basic_x_access_token(fake_github, token):
    fake_github.add_repo("gmf/svc", tree={"a.txt": "1"})
    cred = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    ok = run_git("-c", f"http.extraHeader=Authorization: Basic {cred}", "ls-remote",
                 f"{fake_github.url}/gmf/svc.git")
    assert ok.returncode == 0 and "refs/heads/main" in ok.stdout
    bad = base64.b64encode(b"x-access-token:wrong").decode()
    assert run_git("-c", f"http.extraHeader=Authorization: Basic {bad}", "ls-remote",
                   f"{fake_github.url}/gmf/svc.git").returncode != 0


async def test_fake_github_git_401_challenges_and_unknown_repo_fails(fake_github, token):
    url = f"{fake_github.url}/gmf/x.git/info/refs?service=git-upload-pack"
    async with httpx.AsyncClient() as c:
        r = await c.get(url)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
        r = await c.get(url, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404  # git http-backend: no such repository


async def test_fake_github_stop_removes_its_root():
    from api.tests.fakes.github import FakeGitHub

    with FakeGitHub() as g:
        root = g.root
        assert root.exists()
    assert not root.exists()


async def test_fake_github_add_repo_from_a_directory(fake_github, tmp_path):
    (tmp_path / "t" / "d").mkdir(parents=True)
    (tmp_path / "t" / "d" / "f.txt").write_text("f")
    fake_github.add_repo("gmf/dir", tree=tmp_path / "t")
    assert fake_github.branch_files("gmf/dir", "main") == {"d/f.txt": "f"}


# ------------------------------------------------------------------ Confluence


async def test_fake_confluence_version_rule(fake_confluence_dc):
    pid = fake_confluence_dc.create_page("SPACE", "T", "<p/>")
    assert (await put_page(fake_confluence_dc, pid, version=3)).status_code == 409
    ok = await put_page(fake_confluence_dc, pid, version=2)
    assert ok.status_code == 200 and ok.json()["version"]["number"] == 2
    fake_confluence_dc.bump_version(pid, 5)
    assert (await put_page(fake_confluence_dc, pid, version=3)).status_code == 409
    assert (await put_page(fake_confluence_dc, pid, version=8)).status_code == 200


async def test_fake_confluence_v1_create_get_and_auth(fake_confluence_dc):
    h = bearer(fake_confluence_dc)
    async with httpx.AsyncClient(base_url=fake_confluence_dc.url) as c:
        assert (await c.get("/rest/api/user/current")).status_code == 401
        assert (await c.get("/rest/api/user/current", headers=h)).status_code == 200
        r = await c.post("/rest/api/content", headers=h, json={
            "type": "page", "title": "P", "space": {"key": "S"}, "ancestors": [{"id": "7"}],
            "body": {"storage": {"value": "<p>b</p>", "representation": "storage"}}})
        assert r.status_code == 200 and r.json()["version"]["number"] == 1
        got = (await c.get(f"/rest/api/content/{r.json()['id']}?expand=version", headers=h)).json()
        assert got["title"] == "P" and got["ancestors"] == [{"id": "7"}] and "body" not in got
        full = (await c.get(f"/rest/api/content/{r.json()['id']}?expand=version,body.storage", headers=h)).json()
        assert full["body"]["storage"]["value"] == "<p>b</p>"
        assert (await c.get("/wiki/rest/api/user/current", headers=h)).status_code == 404  # Cloud-only path
        assert (await c.get("/wiki/api/v2/pages", headers=h)).status_code == 404
        assert (await c.get("/rest/api/content/999", headers=h)).status_code == 404
        up = await c.post(f"/rest/api/content/{r.json()['id']}/child/attachment", headers=h,
                          files={"file": ("diagram.png", b"png-bytes", "image/png")})
        assert up.status_code == 403 and "XSRF" in up.text
        up = await c.post(f"/rest/api/content/{r.json()['id']}/child/attachment",
                          headers={**h, "X-Atlassian-Token": "no-check"},
                          files={"file": ("diagram.png", b"png-bytes", "image/png")})
        assert up.json()["results"][0]["title"] == "diagram.png"
        listed = (await c.get(f"/rest/api/content/{r.json()['id']}/child/attachment", headers=h)).json()
        assert [a["title"] for a in listed["results"]] == ["diagram.png"]


async def test_fake_confluence_cloud_v2_and_basic_auth(fake_confluence_cloud):
    f = fake_confluence_cloud
    async with httpx.AsyncClient(base_url=f.url) as c:
        assert (await c.get("/wiki/rest/api/user/current", headers=bearer(f))).status_code == 401
        assert (await c.get("/wiki/rest/api/user/current", headers=basic(f))).status_code == 200
        made = await c.post("/wiki/api/v2/pages", headers=basic(f), json={
            "spaceId": "42", "status": "current", "title": "P", "parentId": "9",
            "body": {"representation": "storage", "value": "<p>1</p>"}})
        pid = made.json()["id"]
        assert made.json()["version"]["number"] == 1
        bad = await c.put(f"/wiki/api/v2/pages/{pid}", headers=basic(f), json={
            "id": pid, "status": "current", "title": "P", "version": {"number": 3},
            "body": {"representation": "storage", "value": "x"}})
        assert bad.status_code == 409
        good = await c.put(f"/wiki/api/v2/pages/{pid}", headers=basic(f), json={
            "id": pid, "status": "current", "title": "P", "version": {"number": 2},
            "body": {"representation": "storage", "value": "<p>2</p>"}})
        assert good.status_code == 200
        got = (await c.get(f"/wiki/api/v2/pages/{pid}?body-format=storage", headers=basic(f))).json()
        assert got["body"]["storage"]["value"] == "<p>2</p>" and got["version"]["number"] == 2
        assert "body" not in (await c.get(f"/wiki/api/v2/pages/{pid}", headers=basic(f))).json()
        assert (await c.get("/rest/api/user/current", headers=basic(f))).status_code == 404  # DC-only path


# ------------------------------------------------------------------ Jira


async def test_fake_jira_issues(fake_jira):
    h = bearer(fake_jira)
    async with httpx.AsyncClient(base_url=fake_jira.url) as c:
        assert (await c.get("/rest/api/2/myself")).status_code == 401
        assert (await c.get("/rest/api/2/myself", headers=h)).status_code == 200
        body = {"fields": {"project": {"key": "GMF"}, "summary": "S", "issuetype": {"name": "Task"}}}
        assert (await c.post("/rest/api/2/issue", headers=h, json=body)).json()["key"] == "GMF-1"
        assert (await c.post("/rest/api/2/issue", headers=h, json=body)).json()["key"] == "GMF-2"
        assert (await c.put("/rest/api/2/issue/GMF-1", headers=h,
                            json={"fields": {"summary": "S2"}})).status_code == 204
        assert (await c.get("/rest/api/2/issue/GMF-1", headers=h)).json()["fields"]["summary"] == "S2"
        fake_jira.delete("GMF-1")
        assert (await c.get("/rest/api/2/issue/GMF-1", headers=h)).status_code == 404
        assert (await c.put("/rest/api/2/issue/GMF-1", headers=h, json={"fields": {}})).status_code == 404


# ------------------------------------------------------------------ Figma


async def test_fake_figma_file_has_two_pages_and_three_frames(fake_figma):
    data = await get_json(fake_figma, "/v1/files/FILE1")
    pages = data["document"]["children"]
    assert len(pages) == 2
    assert sum(1 for p in pages for n in p["children"] if n["type"] == "FRAME") == 3
    nodes = await get_json(fake_figma, "/v1/files/FILE1/nodes?ids=1:2,9:9")
    assert nodes["nodes"]["1:2"]["document"]["name"] == "Account Summary" and nodes["nodes"]["9:9"] is None
    assert (await get_json(fake_figma, "/v1/me"))["handle"] == "exiqo-bot"


async def test_fake_figma_requires_the_token_and_knows_its_files(fake_figma):
    async with httpx.AsyncClient(base_url=fake_figma.url) as c:
        assert (await c.get("/v1/me")).status_code == 403
        assert (await c.get("/v1/files/NOPE", headers={"X-Figma-Token": fake_figma.token})).status_code == 404


async def test_fake_figma_images_point_at_the_second_host(fake_figma, fake_figma_images):
    urls = (await get_json(fake_figma, "/v1/images/FILE1?ids=1:2&format=png"))["images"]
    assert urlparse(urls["1:2"]).netloc == fake_figma_images.host
    async with httpx.AsyncClient() as c:
        r = await c.get(urls["1:2"])
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    assert fake_figma_images.host != fake_figma.host


# ------------------------------------------------------------------ settings builder


async def test_connectors_settings_points_at_the_fakes(connectors_settings, fake_github, fake_jira,
                                                       fake_confluence, fake_figma):
    from api.app.connectors.config import load_install, secret

    s = connectors_settings(github=fake_github, confluence=fake_confluence, jira=fake_jira, figma=fake_figma)
    install = load_install(s)
    assert install["github"].enabled and install["github"].allowed_hosts == [fake_github.host]
    assert install["atlassian"].jira_url == fake_jira.url
    assert install["figma"].image_hosts == [fake_figma.images.host]
    assert s.connectors_allow_insecure_loopback
    assert secret("github_token", s) == fake_github.token
    assert secret("figma_token", s) == fake_figma.token
    assert secret("atlassian_token", s) == fake_jira.token
