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
