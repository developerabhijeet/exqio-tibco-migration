"""demo_check.py memory step against a fake API."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check as dc

ROWS = ["memory available", "revisions per phase", "lineage DealerFundingRequest",
        "search DealerFundingRequest", "lineage CreditApplicationIntake",
        "search CreditApplicationIntake", "latest diff", "curation applied", "ask cites memory"]


def fake(available: bool = True):
    state = {"curated": False}
    revs = [{"n": i + 1, "phase": p} for i, p in
            enumerate(["discover", "analysis", "analysis", "design", "design", "build", "build"])]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/status"):
            return httpx.Response(200, json={"configured": True, "available": available,
                                             "revision": 7, "counts": {"Process": 4}})
        if path.endswith("/revisions"):
            extra = [{"n": 8, "phase": "curation"}] if state["curated"] else []
            return httpx.Response(200, json={"revisions": extra + revs[::-1]})
        if "/diff" in path:
            return httpx.Response(200, json={"nodes": {"added": [{}], "changed": [], "removed": []},
                                             "relationships": {"added": [], "changed": [], "removed": []}})
        if path.endswith("/processes"):
            return httpx.Response(200, json=[{"name": t, "id": f"id-{t}", "ir_id": f"ir-{t}"}
                                             for t in dc.TARGETS])
        if path.endswith("/node"):
            return httpx.Response(200, json={"lineage": [{"edge": {"type": "MIGRATED_TO"}}]})
        if path.endswith("/search"):
            return httpx.Response(200, json={"hits": [{"label": "Process",
                                                       "props": {"name": request.url.params["q"]}}]})
        if path.endswith("/curations"):
            assert request.headers["X-User"] == dc.SME
            state["curated"] = True
            return httpx.Response(202, json={"event_id": "e", "job_id": "j"})
        if path.endswith("/ask"):
            return httpx.Response(200, json={"citations": [{"citation": {"uid": "process:x"}}]})
        return httpx.Response(404)

    return handler


def run_step(available: bool = True) -> list[dc.Row]:
    client = httpx.Client(base_url="http://t", transport=httpx.MockTransport(fake(available)))
    run = dc.Run(api="http://t")
    flow = dc.Flow(run, client, "full", Path("x.zip"))
    flow.project_id = "p1"
    try:
        flow.memory()
    except dc.StepFailed:
        pass
    return run.rows


def test_all_rows_pass_on_good_responses() -> None:
    rows = run_step()
    assert [r.check for r in rows] == ROWS
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


def test_unavailable_memory_stops_the_step() -> None:
    rows = run_step(available=False)
    assert [(r.check, r.ok) for r in rows] == [("memory available", False)]
