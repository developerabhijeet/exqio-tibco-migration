"""demo_check.py exiqoian step against a fake API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check as dc

ROWS = ["conversation created", "answers with a citation", "declines off-topic"]


def sse(*events: tuple[str, dict]) -> str:
    return "".join(f"event: {name}\r\ndata: {json.dumps(data)}\r\n\r\n" for name, data in events)


def done(verdict: str, refs: int) -> tuple[str, dict]:
    return "done", {"message": {"verdict": verdict, "cited": {"text": "x", "spans": [], "refs": [{"id": f"c{i}"} for i in range(refs)]}}}


def fake(answer: tuple[str, int] = ("answer", 2), decline: tuple[str, int] = ("off_topic", 0), created: int = 201):
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["X-User"] == dc.SME
        if path.endswith("/conversations"):
            return httpx.Response(created, json={"id": "c1"})
        if path.endswith("/conversations/c1/messages"):
            text = json.loads(request.content)["text"]
            asked.append(text)
            verdict, refs = answer if text == "What does DealerFundingRequest do?" else decline
            return httpx.Response(200, text=sse(("meta", {}), done(verdict, refs)),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    handler.asked = asked  # type: ignore[attr-defined]
    return handler


def run_step(**kw) -> tuple[list[dc.Row], list[str]]:
    handler = fake(**kw)
    client = httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler))
    run = dc.Run(api="http://t")
    flow = dc.Flow(run, client, "full", Path("x.zip"))
    flow.project_id = "p1"
    try:
        flow.exiqoian()
    except dc.StepFailed:
        pass
    return run.rows, handler.asked


def test_all_rows_pass_on_good_responses() -> None:
    rows, asked = run_step()
    assert [r.check for r in rows] == ROWS and all(r.ok for r in rows), rows
    assert asked == ["What does DealerFundingRequest do?", "what is 2+2"]


def test_an_answer_without_a_citation_fails() -> None:
    rows, _ = run_step(answer=("answer", 0))
    assert [(r.check, r.ok) for r in rows if not r.ok] == [("answers with a citation", False)]


def test_a_non_answer_fails_and_a_missing_decline_fails() -> None:
    rows, _ = run_step(answer=("not_covered", 0), decline=("answer", 1))
    assert {r.check for r in rows if not r.ok} == {"answers with a citation", "declines off-topic"}


def test_no_conversation_stops_the_step() -> None:
    rows, asked = run_step(created=503)
    assert [(r.check, r.ok) for r in rows] == [("conversation created", False)] and asked == []


def test_the_step_runs_right_after_memory() -> None:
    names = [n for n, _, _ in dc.build_steps(dc.Flow(dc.Run(api="x"), httpx.Client(base_url="http://t"), "full", Path("x.zip")))]
    assert names[names.index("memory") + 1] == "exiqoian"
