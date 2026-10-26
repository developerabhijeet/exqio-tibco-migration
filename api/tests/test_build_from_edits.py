"""A build from an edit set (sub-project 6, Task 8): the fresh generation, the hand edits merged in
(stage ``hand edits``), ``origin = hand`` in ``build_files``, agents that leave hand files alone,
and the infrastructure-only resume."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from api.app.models import Build, JobEvent
from api.app.services import build as build_svc
from api.app.services import fake_responses
from api.tests import test_build_verify as build_tests
from api.tests.test_edit_sets import AGENT_FILE, ENGINEER, TEST_FILE, download, edit, post_edits
from engine.agents.reviewers import REVIEWERS

scenario_db = build_tests.scenario_db
DFR = "dealerfunding.DealerFundingRequest"
EDIT_LINE = "\n// hand edit: the engineer's body\n"


@pytest.fixture
def engineer() -> dict[str, str]:
    return ENGINEER


@pytest.fixture
def recording_provider(monkeypatch):
    """The fake provider, recording each prompt per agent (builder, test_engineer, repair, ...)."""
    names = {"build": "builder", "test": "test_engineer"}

    class Recorder:
        def __init__(self) -> None:
            self.prompts: dict[str, list[str]] = {}

        def files_sent_to(self, agent: str) -> str:
            return "\n".join(self.prompts.get(agent, []))

        def clear(self) -> None:
            self.prompts.clear()

    rec = Recorder()
    original = fake_responses.reply_for

    def reply_for(req, process):
        user = "".join(str(m.get("content", "")) for m in req.messages if m.get("role") == "user")
        stage = str(req.tags.get("stage"))
        rec.prompts.setdefault(names.get(stage, stage), []).append(user)
        return original(req, process)

    monkeypatch.setattr(fake_responses, "reply_for", reply_for)
    return rec


@pytest.fixture
async def baseline_build(client, monkeypatch) -> str:
    _, _, build_id, _ = await build_tests._built(client, monkeypatch, DFR)
    return build_id


@pytest.fixture
async def edit_set_two_files(client, baseline_build) -> str:
    z = edit(await download(client, baseline_build),
             {AGENT_FILE: lambda t: t + EDIT_LINE, TEST_FILE: lambda t: t + "\n// hand edit: the engineer's test\n"})
    r = await post_edits(client, baseline_build, z)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def wait_terminal():
    async def run(client, job_id: str) -> dict:
        job = await build_tests._wait_job(client, job_id, timeout=900)
        assert job["status"] == "done", job
        return job["result"]

    return run


async def file_text(client, build_id: str, path: str) -> str:
    r = await client.get(f"/builds/{build_id}/files/{path}")
    assert r.status_code == 200, r.text
    return r.json()["content"]


async def baseline_text(client, edit_set_id: str, path: str) -> str:
    return (await client.get(f"/edit-sets/{edit_set_id}/diff/{path}")).json()["baseline"]


# ------------------------------------------------------------------ the brief's tests
async def test_resume_builds_with_hand_files(client, edit_set_two_files, engineer, wait_terminal):
    EDITED_AGENT_TEXT = (await baseline_text(client, edit_set_two_files, AGENT_FILE)) + EDIT_LINE
    job = (await client.post(f"/edit-sets/{edit_set_two_files}/resume", headers=engineer)).json()
    build = await wait_terminal(client, job["job_id"])
    files = {f["path"]: f["origin"] for f in (await client.get(f"/builds/{build['build_id']}/files")).json()["files"]}
    assert files[AGENT_FILE] == "hand" and files[TEST_FILE] == "hand"
    assert (await file_text(client, build["build_id"], AGENT_FILE)) == EDITED_AGENT_TEXT


async def test_builder_does_not_fill_a_hand_file(client, recording_provider, edit_set_two_files, engineer, wait_terminal):
    assert AGENT_FILE in recording_provider.files_sent_to("builder")  # the baseline build asked for it
    recording_provider.clear()
    await wait_terminal(client, (await client.post(f"/edit-sets/{edit_set_two_files}/resume", headers=engineer)).json()["job_id"])
    assert AGENT_FILE not in recording_provider.files_sent_to("builder")
    assert TEST_FILE not in recording_provider.files_sent_to("test_engineer")


# ------------------------------------------------------------------ the build record
async def stages(client, job_id: str) -> list[str]:
    async with client._transport.app.state.sessionmaker() as s:
        rows = (await s.scalars(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq))).all()
    return [e.text for e in rows if e.event == "stage"]


async def test_hand_edits_stage_and_record(client, edit_set_two_files, engineer, wait_terminal):
    job = (await client.post(f"/edit-sets/{edit_set_two_files}/resume", headers=engineer)).json()
    result = await wait_terminal(client, job["job_id"])
    names = await stages(client, job["job_id"])
    assert names[:3] == ["design check", "generate", "hand edits"]
    view = (await client.get(f"/builds/{result['build_id']}")).json()
    assert {(h["path"], h["outcome"]) for h in view["builder"]["hand_edits"]} == {(AGENT_FILE, "kept"), (TEST_FILE, "kept")}
    assert view["iteration"] == (await client.get(f"/edit-sets/{edit_set_two_files}")).json()["iteration"]
    assert set(REVIEWERS) <= set(view["builder"]["reviewers"])  # the panel still reviews the hand file


async def test_a_normal_build_has_no_hand_edits_stage(client, baseline_build):
    async with client._transport.app.state.sessionmaker() as s:
        job_id = (await s.get(Build, baseline_build)).job_id
    assert "hand edits" not in await stages(client, job_id)
    assert "hand_edits" not in ((await client.get(f"/builds/{baseline_build}")).json()["builder"] or {})


async def test_an_infra_edit_survives_the_infrastructure_generation(client, baseline_build, engineer, wait_terminal):
    """Infra-only: the baseline tree is copied, code stages are skipped, the hand Dockerfile wins."""
    z = edit(await download(client, baseline_build), {"Dockerfile": lambda t: t + "\n# hand note\n"})
    es = (await post_edits(client, baseline_build, z)).json()
    assert es["resume_phase"] == "infra"
    job = (await client.post(f"/edit-sets/{es['id']}/resume", headers=engineer)).json()
    result = await wait_terminal(client, job["job_id"])
    build_id = result["build_id"]
    assert (await file_text(client, build_id, "Dockerfile")).endswith("\n# hand note\n")
    files = {f["path"]: f["origin"] for f in (await client.get(f"/builds/{build_id}/files")).json()["files"]}
    base = {f["path"]: f for f in (await client.get(f"/builds/{baseline_build}/files")).json()["files"]}
    assert files["Dockerfile"] == "hand" and files[TEST_FILE] == base[TEST_FILE]["origin"] == "ai"
    assert set(files) == set(base)
    assert (await file_text(client, build_id, TEST_FILE)) == (await file_text(client, baseline_build, TEST_FILE))
    view = (await client.get(f"/builds/{build_id}")).json()
    skipped = {s["name"]: s for s in view["steps"]}
    assert all(skipped[n]["skipped"] and "infrastructure-only edits" in skipped[n]["log"]
               for n in ("restore", "build", "format", "test"))
    names = await stages(client, job["job_id"])
    assert names.index("infra") > names.index("repair") and "infra validate" in names


async def test_infra_edit_in_a_code_build_is_merged_after_the_infrastructure(client, baseline_build, engineer,
                                                                             wait_terminal):
    z = edit(await download(client, baseline_build), {"Dockerfile": lambda t: t + "\n# hand note\n",
                                                       AGENT_FILE: lambda t: t + EDIT_LINE})
    es = (await post_edits(client, baseline_build, z)).json()
    assert es["resume_phase"] == "build"
    job = (await client.post(f"/edit-sets/{es['id']}/resume", headers=engineer)).json()
    build_id = (await wait_terminal(client, job["job_id"]))["build_id"]
    assert (await file_text(client, build_id, "Dockerfile")).endswith("\n# hand note\n")
    files = {f["path"]: f["origin"] for f in (await client.get(f"/builds/{build_id}/files")).json()["files"]}
    assert files["Dockerfile"] == "hand" and files[AGENT_FILE] == "hand"
    assert any(o == "infra" for p, o in files.items() if p.startswith("infra/"))


async def test_resume_without_the_baseline_folder_keeps_the_edits(client, app, edit_set_two_files, engineer,
                                                                  wait_terminal, baseline_build):
    async with app.state.sessionmaker() as s:
        (await s.get(Build, baseline_build)).output_dir = None
        await s.commit()
    job = (await client.post(f"/edit-sets/{edit_set_two_files}/resume", headers=engineer)).json()
    result = await wait_terminal(client, job["job_id"])
    view = (await client.get(f"/builds/{result['build_id']}")).json()
    by_path = {h["path"]: h for h in view["builder"]["hand_edits"]}
    assert by_path[AGENT_FILE]["detail"] == "baseline folder missing: edit kept as uploaded"


def test_stages_for_a_normal_build_are_unchanged():
    assert build_svc.STAGES == ["design check", "generate", "tests", "test engineer", "builder", "review", "restore",
                                "build", "format", "test", "repair", "infra", "infra validate", "metrics", "persist"]
    assert build_svc.EDIT_STAGES == [*build_svc.STAGES[:2], "hand edits", *build_svc.STAGES[2:]]
