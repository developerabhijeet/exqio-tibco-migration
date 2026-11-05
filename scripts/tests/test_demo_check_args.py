"""demo_check.py against an installed platform: --base-url and --token, local behaviour untouched."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check as dc


def test_base_url_is_the_api_under_the_ingress_prefix() -> None:
    assert dc.api_url_from_base("https://exiqo.apps.example") == "https://exiqo.apps.example/api"
    assert dc.api_url_from_base("https://exiqo.apps.example/") == "https://exiqo.apps.example/api"


def test_local_default_is_unchanged_without_the_flags(monkeypatch) -> None:
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.setenv("API_PORT", "9123")
    assert dc.default_api_url() == "http://localhost:9123"


def test_an_oidc_install_without_a_token_gets_a_hint(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "sign in required"})

    real = httpx.Client
    monkeypatch.setattr(dc.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.delenv("EXIQO_TOKEN", raising=False)
    assert dc.main(["--base-url", "https://exiqo.example"]) == 2
    assert "--token" in capsys.readouterr().out


def test_the_token_is_sent_as_a_bearer_and_never_printed(monkeypatch, capsys) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(403, json={"detail": "no role"})

    real = httpx.Client
    monkeypatch.setattr(dc.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    dc.main(["--base-url", "https://exiqo.example", "--token", "s3cr3t-token"])
    assert seen == ["Bearer s3cr3t-token"]
    assert "s3cr3t-token" not in capsys.readouterr().out


TOOLCHAIN_OFF = "not run: the build toolchain is switched off (BUILD_TOOLCHAIN=false)."


def _toolchain_off_api(request: httpx.Request) -> httpx.Response:
    """An API whose workers run with BUILD_TOOLCHAIN=false (the kind end to end)."""
    path = request.url.path
    if path == "/processes/p1/build":
        return httpx.Response(202, json={"build_id": "b1", "job_id": "j1"})
    if path == "/jobs/j1":
        return httpx.Response(200, json={"id": "j1", "kind": "build", "status": "done"})
    if path == "/builds/b1":
        steps = [{"name": n, "ok": False, "skipped": True, "log": TOOLCHAIN_OFF}
                 for n in ("build", "format", "test")]
        steps.insert(0, {"name": "restore", "ok": False, "skipped": True, "log": TOOLCHAIN_OFF})
        return httpx.Response(200, json={
            "status": "failed", "steps": steps,
            "builder": {"test_engineer": {"filled": [], "rejections": [], "warnings": []}},
            "review_findings": {},
            "g4": {"enabled": False, "reason": "compile did not pass (restore failed)"},
            "infra_validation": {"ok": False, "fmt_ok": True, "hclfmt_ok": True,
                                 "registry_unreachable": True, "notes": ["registry unreachable"],
                                 "modules": [{"name": "app", "registry_unreachable": True}]}})
    if path == "/builds/b1/tests":
        # a test the test engineer could not fill has no body: listed, not counted as not run
        return httpx.Response(200, json={"summary": {"total": 4, "passed": 0, "failed": 0, "not_run": 3,
                                                     "no_body": 1, "blocked": 0}})
    if path == "/builds/b1/files":
        return httpx.Response(200, json={"counts": {"code": 10}})
    if path == "/builds/b1/infra":
        return httpx.Response(200, json={"files": [{"path": "infra/live/dev/terragrunt.hcl"},
                                                   {"path": "infra/modules/app/main.tf"}]})
    if path == "/builds/b1/run" and request.method == "GET":
        return httpx.Response(200, json={"running": False})
    if path == "/builds/b1/run":
        return httpx.Response(409, json={"detail": "the build did not compile"})
    if path == "/builds/b1/verify":
        return httpx.Response(409, json={"detail": "no service running"})
    if path == "/builds/b1/parity":
        return httpx.Response(404, json={"detail": "no parity run"})
    if path == "/processes/p1/scenarios/generate":
        return httpx.Response(200, json={"scenarios": [{}], "label": "derived from the TIBCO model"})
    return httpx.Response(404, json={"detail": f"unexpected {path}"})


def test_toolchain_off_mode_expects_the_honest_skipped_states() -> None:
    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(_toolchain_off_api))
    run = dc.Run("http://api")
    flow = dc.Flow(run, client, "toolchain-off", Path("unused.zip"))
    flow.process_ids = {"DealerFundingRequest": "p1"}
    flow.build("DealerFundingRequest")
    flow.infra("DealerFundingRequest")
    flow.verify("DealerFundingRequest")
    failed = [(r.step, r.check, r.detail) for r in run.rows if not r.ok]
    assert not failed
    checks = {r.check for r in run.rows}
    assert {"restore, build, format and test skipped", "G4 not enabled, reason given",
            "start refused", "verify refused"} <= checks


def test_toolchain_off_mode_fails_when_the_toolchain_actually_ran() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response = _toolchain_off_api(request)
        if request.url.path == "/builds/b1":
            body = response.json()
            body["steps"][0] = {"name": "restore", "ok": True, "skipped": False, "log": "restored"}
            return httpx.Response(200, json=body)
        return response

    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    run = dc.Run("http://api")
    flow = dc.Flow(run, client, "toolchain-off", Path("unused.zip"))
    flow.process_ids = {"DealerFundingRequest": "p1"}
    flow.build("DealerFundingRequest")
    assert [r.check for r in run.rows if not r.ok] == ["restore, build, format and test skipped"]


def test_toolchain_off_is_a_mode_choice() -> None:
    import pytest

    with pytest.raises(SystemExit):
        dc.main(["--mode", "nonsense"])
    assert "toolchain-off" in dc.MODES
