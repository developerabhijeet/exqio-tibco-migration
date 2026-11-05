"""demo_check.py iteration step (sub-project 6, Task 12) against a fake API."""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check as dc

AGENT = "src/Gmf.Core/Rules/DealerFundingRequestConditions.cs"
ROWS = ["iteration opened by architect.lee", "design job finished", "G3 pending again",
        "iteration designing or later", "a build to edit", "build downloaded", "one agent file edited",
        "preview lists exactly that file", "memory revision of iteration 2"]


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Gmf.sln", "sln")
        zf.writestr(AGENT, "class C {}\n")
        zf.writestr("src/Gmf.Core/Program.cs", "class P {}\n")
        zf.writestr("EXIQO_BASELINE.json", "{}")
    return buf.getvalue()


def fake(memory: bool = True, edited_paths: list[str] | None = None):
    seen: dict[str, list] = {"iterations": [], "uploads": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if re.fullmatch(r"/processes/[^/]+/iterations", path) and request.method == "POST":
            seen["iterations"].append(json.loads(request.content))
            assert request.headers["X-User"] == dc.ARCHITECT
            return httpx.Response(201, json={"id": "it2", "n": 2, "status": "open", "job_id": "j-design"})
        if path == "/jobs/j-design":
            return httpx.Response(200, json={"id": "j-design", "kind": "design", "status": "done"})
        if path.endswith("/gates"):
            return httpx.Response(200, json=[{"gate": "G1", "state": "approved"}, {"gate": "G3", "state": "pending"}])
        if path == "/iterations/it2":
            return httpx.Response(200, json={"id": "it2", "n": 2, "status": "designing", "inputs": [{}]})
        if path == "/builds/b1/download":
            return httpx.Response(200, content=_zip())
        if path == "/builds/b1/files":
            assert request.url.params["origin"] == "agent,ai"
            return httpx.Response(200, json={"files": [{"path": AGENT, "origin": "agent"}]})
        if path == "/builds/b1/edits":
            assert request.headers["X-User"] == dc.ENGINEER
            body = request.content
            with zipfile.ZipFile(io.BytesIO(body[body.index(b"PK"):])) as zf:
                seen["uploads"].append(zf.read(AGENT).decode())
            files = edited_paths if edited_paths is not None else [AGENT]
            return httpx.Response(201, json={"id": "es", "files": [{"path": p} for p in files],
                                             "resume_phase": "design"})
        if path.endswith("/status"):
            return httpx.Response(200, json={"available": memory})
        if path.endswith("/revisions"):
            assert request.url.params["iteration"] == "2"
            return httpx.Response(200, json={"revisions": [{"n": 31, "phase": "iteration", "iteration": 2}]})
        return httpx.Response(404, json={"detail": f"no fake for {path}"})

    return handler, seen


def run_step(**kw) -> tuple[list[dc.Row], dict, dc.Run]:
    handler, seen = fake(**kw)
    client = httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler))
    run = dc.Run(api="http://t")
    flow = dc.Flow(run, client, "full", Path("x.zip"))
    flow.project_id, flow.process_ids, flow.builds = "p1", {dc.TARGETS[0]: "proc1"}, {dc.TARGETS[0]: "b1"}
    try:
        flow.iteration()
    except dc.StepFailed:
        pass
    return run.rows, seen, run


def test_iteration_step_runs_after_memory_and_exiqoian():
    flow = dc.Flow(dc.Run(api="http://t"), httpx.Client(base_url="http://t"), "full", Path("x.zip"))
    names = [name for name, _, _ in dc.build_steps(flow)]
    assert names.index("memory") + 2 == names.index("exiqoian") + 1 == names.index("iteration")
    assert names.index("iteration") + 1 == names.index("connectors")


def test_all_rows_pass_and_the_requirement_is_sent():
    rows, seen, _ = run_step()
    assert [r.check for r in rows] == ROWS
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]
    assert seen["iterations"][0]["requirements"] == dc.ITERATION_REQUIREMENT
    assert seen["uploads"] == ["class C {}\n" + dc.HAND_EDIT]


def test_without_memory_the_revision_is_not_checked():
    rows, _, run = run_step(memory=False)
    assert rows[-1].check == "preview lists exactly that file" and all(r.ok for r in rows)
    assert any("memory not configured" in n for n in run.notes)


def test_a_preview_with_other_files_fails():
    rows, _, _ = run_step(edited_paths=[AGENT, "src/Gmf.Core/Program.cs"])
    row = next(r for r in rows if r.check == "preview lists exactly that file")
    assert not row.ok
