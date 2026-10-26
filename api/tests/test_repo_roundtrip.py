"""Push an iteration's build to its repository and pull a reviewer's edit back (sub-project 6,
Task 10). Against ``fake_github``; fixtures shared with ``test_repos``."""
from __future__ import annotations

import json

from sqlalchemy import select

from api.app.models import AuditLog
from api.tests import test_repos as repos_tests
from api.tests.test_repos import AGENT_FILE, FILES, PROC, P, replace_body, wait_terminal

gh_settings = repos_tests.gh_settings
app = repos_tests.app
project = repos_tests.project
finished_build = repos_tests.finished_build
engineer = repos_tests.engineer
sme = repos_tests.sme
connected_build_repo = repos_tests.connected_build_repo


async def test_push_then_pull_reviewer_change_is_one_edit(client, fake_github, connected_build_repo, finished_build, engineer):
    out = (await client.post(f"/repos/{connected_build_repo}/push", json={"build_id": finished_build}, headers=engineer)).json()
    assert out["branch"] == "exiqo/dealer-funding-request/iteration-1" and out["pull_request"]["number"] == 1
    fake_github.commit(out["branch"], {AGENT_FILE: replace_body})
    job = (await client.post(f"/repos/{connected_build_repo}/pull", json={"branch": out["branch"]}, headers=engineer)).json()
    es = (await client.get(f"/edit-sets/{(await wait_terminal(client, job['job_id']))['id']}")).json()
    assert [f["path"] for f in es["files"]] == [AGENT_FILE] and es["baseline_build_id"] == finished_build


async def test_the_pushed_branch_carries_the_baseline(client, fake_github, connected_build_repo, finished_build,
                                                      engineer):
    out = (await client.post(f"/repos/{connected_build_repo}/push", json={"build_id": finished_build, "open_pr": False},
                             headers=engineer)).json()
    assert out["pull_request"] is None and len(out["commit"]) == 40
    files = fake_github.branch_files("gmf/svc", out["branch"])
    manifest = json.loads(files["EXIQO_BASELINE.json"])
    assert manifest["build_id"] == finished_build and AGENT_FILE in manifest["files"]
    assert "EXPORTED_FROM.md" in files and AGENT_FILE in files
    async with client._transport.app.state.sessionmaker() as s:
        rows = (await s.scalars(select(AuditLog).where(AuditLog.action == "connector github push"))).all()
    assert [r.detail["outcome"] for r in rows] == ["ok"] and rows[0].detail["branch"] == out["branch"]


async def test_a_build_of_another_process_is_refused(client, app, fake_github, connected_build_repo, finished_build,
                                                     engineer):
    from api.app.models import Build, Process

    async with app.state.sessionmaker() as s:
        proc = await s.get(Process, PROC)
        other = Process(project_id=P, application_id=proc.application_id, upload_id=proc.upload_id, ir_id="x.Other",
                        name="Other", dialect="bw5", source_path="o.process", ir={}, spec_hash="o" * 64)
        s.add(other)
        await s.flush()
        b = Build(process_id=other.id, status="passed", output_dir="/nonexistent")
        s.add(b)
        await s.commit()
        other_build = b.id
    r = await client.post(f"/repos/{connected_build_repo}/push", json={"build_id": other_build}, headers=engineer)
    assert r.status_code == 422 and r.json()["detail"] == "this repository is connected to Dealer Funding Request"


async def test_push_needs_an_engineer(client, connected_build_repo, finished_build, sme):
    r = await client.post(f"/repos/{connected_build_repo}/push", json={"build_id": finished_build}, headers=sme)
    assert r.status_code == 403


async def test_an_estate_repository_takes_no_build(client, fake_github, project, finished_build, engineer):
    fake_github.add_repo("gmf/estate", tree={"a.txt": "a"})
    r = await client.post(f"/projects/{P}/repos", json={"repo": "gmf/estate", "branch": "main", "purpose": "estate"},
                          headers=engineer)
    await wait_terminal(client, r.json()["job_id"])
    r = await client.post(f"/repos/{r.json()['id']}/push", json={"build_id": finished_build}, headers=engineer)
    assert r.status_code == 422 and r.json()["detail"] == "an estate repository takes no build: connect a build repository"


async def test_the_sp7_push_job_also_writes_the_baseline(client, app, fake_github, finished_build, engineer):
    """The Build tab's "push to GitHub" (sub-project 7) ships EXIQO_BASELINE.json too, so a later
    pull of that branch is an edit set."""
    fake_github.add_repo("gmf/credit", tree={"README.md": "x"})
    r = await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"}, headers=engineer)
    assert r.status_code == 202, r.text
    out = await wait_terminal(client, r.json()["job_id"])
    files = fake_github.branch_files("gmf/credit", out["branch"])
    assert json.loads(files["EXIQO_BASELINE.json"])["build_id"] == finished_build


# ------------------------------------------------------------------ final review fixes
async def test_a_second_push_never_overwrites_reviewer_commits(client, fake_github, connected_build_repo,
                                                               finished_build, engineer):  # final I3
    first = (await client.post(f"/repos/{connected_build_repo}/push", json={"build_id": finished_build},
                               headers=engineer)).json()
    reviewer = fake_github.commit(first["branch"], {AGENT_FILE: replace_body}, repo="gmf/svc")
    again = await client.post(f"/repos/{connected_build_repo}/push", json={"build_id": finished_build}, headers=engineer)
    assert again.status_code == 409 and "pull the reviewer's edits first" in again.json()["detail"]
    assert fake_github.branch_files("gmf/svc", first["branch"])[AGENT_FILE] == replace_body(FILES[AGENT_FILE])
    pulled = (await client.post(f"/repos/{connected_build_repo}/pull", json={"branch": first["branch"]},
                                headers=engineer)).json()
    assert pulled["commit"] == reviewer
    await wait_terminal(client, pulled["job_id"])
    assert (await client.post(f"/repos/{connected_build_repo}/push", json={"build_id": finished_build},
                              headers=engineer)).status_code == 200  # the reviewer's commit was fetched first


async def test_a_push_onto_a_non_empty_base_ships_exactly_the_build(client, fake_github, finished_build, engineer):  # I4
    fake_github.add_repo("gmf/live", branch="main", tree={"README.md": "# GMF\n", ".github/workflows/ci.yml": "on: push\n",
                                                          "src/Credit/Old.cs": "class Old {}\n", "LICENSE": "MIT\n"})
    r = await client.post(f"/projects/{P}/repos", headers=engineer,
                          json={"repo": "gmf/live", "branch": "main", "purpose": "build", "process_id": PROC})
    await wait_terminal(client, r.json()["job_id"])
    out = (await client.post(f"/repos/{r.json()['id']}/push", json={"build_id": finished_build}, headers=engineer)).json()
    files = set(fake_github.branch_files("gmf/live", out["branch"]))
    assert files == {*FILES, "EXIQO_BASELINE.json", "EXPORTED_FROM.md", "README.md", ".github/workflows/ci.yml",
                     "LICENSE"}  # the stale Old.cs is removed; the kept repository files stay
    fake_github.commit(out["branch"], {AGENT_FILE: replace_body}, repo="gmf/live")
    job = (await client.post(f"/repos/{r.json()['id']}/pull", json={"branch": out["branch"]}, headers=engineer)).json()
    es = (await client.get(f"/edit-sets/{(await wait_terminal(client, job['job_id']))['id']}")).json()
    assert [f["path"] for f in es["files"]] == [AGENT_FILE]  # README, LICENSE and the workflow are no edits
