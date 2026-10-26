"""demo_check.py --stack: the project is created on the stack, the build is checked against it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check as dc


def test_stack_flag_is_passed_to_project_creation() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(500)

    client = httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler))
    flow = dc.Flow(dc.Run("http://t"), client, "full", Path("x.zip"), stack="go")
    with pytest.raises(dc.StepFailed):
        flow.upload()
    assert seen[0]["target_stack"] == "go"


def test_main_hands_the_stack_to_the_flow(monkeypatch, capsys) -> None:
    class Stub:
        project_id = ""

        def __init__(self, run, client, mode, zip_path, token=False, stack="dotnet") -> None:
            Stub.stack = stack
            run.check("stub", "constructed", True)

        def __getattr__(self, name):
            return lambda *a, **k: None

    real = httpx.Client
    monkeypatch.setattr(dc.httpx, "Client", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), **kw))
    monkeypatch.setattr(dc, "Flow", Stub)
    monkeypatch.setattr(dc, "nuget_probe", lambda: (True, "ok"))
    monkeypatch.setattr(dc, "registry_probe", lambda v: (True, "ok"))
    dc.main(["--api", "http://t", "--stack", "java", "--mode", "full"])
    assert Stub.stack == "java"
    assert "target stack: java" in capsys.readouterr().out


def test_unknown_stack_is_refused() -> None:
    with pytest.raises(SystemExit):
        dc.main(["--stack", "cobol"])


def _build_api(stack: str, served: str):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/processes/p1/build":
            return httpx.Response(202, json={"build_id": "b1", "job_id": "j1"})
        if path == "/jobs/j1":
            return httpx.Response(200, json={"status": "done", "kind": "build"})
        if path == "/builds/b1":
            return httpx.Response(200, json={
                "stack": served, "status": "passed",
                "steps": [{"name": n, "ok": True, "log": "ok"} for n in ("restore", "build", "format", "test")],
                "builder": {"test_engineer": {"filled": [], "rejections": [], "warnings": []}},
                "review_findings": {}, "g4": {"enabled": True}})
        if path == "/builds/b1/tests":
            return httpx.Response(200, json={"summary": {"total": 3, "passed": 3, "failed": 0, "not_run": 0}})
        if path == "/builds/b1/files":
            return httpx.Response(200, json={"counts": {"code": 4}})
        if path == "/builds/b1/metrics":
            return httpx.Response(200, json={k: 1 for k in dc.METRIC_KEYS})
        return httpx.Response(404)
    return handler


@pytest.mark.parametrize("stack", ["java", "go", "python"])
def test_build_asserts_stack_and_metrics(stack) -> None:
    client = httpx.Client(base_url="http://t", transport=httpx.MockTransport(_build_api(stack, stack)))
    run = dc.Run("http://t")
    flow = dc.Flow(run, client, "full", Path("x.zip"), stack=stack)
    flow.process_ids = {"DealerFundingRequest": "p1"}
    flow.build("DealerFundingRequest")
    assert not [r for r in run.rows if not r.ok], [r for r in run.rows if not r.ok]
    assert {"build stack", "metrics keys"} <= {r.check for r in run.rows}


def test_build_on_another_stack_fails() -> None:
    client = httpx.Client(base_url="http://t", transport=httpx.MockTransport(_build_api("go", "dotnet")))
    run = dc.Run("http://t")
    flow = dc.Flow(run, client, "full", Path("x.zip"), stack="go")
    flow.process_ids = {"DealerFundingRequest": "p1"}
    flow.build("DealerFundingRequest")
    assert [r.check for r in run.rows if not r.ok] == ["build stack"]


def test_preflight_refuses_an_unselectable_stack() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/stacks":
            return httpx.Response(200, json={"stacks": [{"key": "go", "selectable": False,
                                                         "reason": "go is not enabled on this install"}]})
        return httpx.Response(200, json={})

    client = httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler))
    run = dc.Run("http://t")
    flow = dc.Flow(run, client, "full", Path("x.zip"), stack="go")
    with pytest.raises(dc.StepFailed):
        flow.preflight()
    assert any(r.check == "go stack selectable" and not r.ok and "not enabled" in r.detail for r in run.rows)
