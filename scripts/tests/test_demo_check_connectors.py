"""demo_check.py connectors step against a fake API."""
from __future__ import annotations

from conftest import run_demo_check


def test_connectors_step_skips_when_none_enabled(fake_api) -> None:
    out = run_demo_check(["--base-url", fake_api.url])
    assert "connectors: SKIP (none enabled)" in out
    assert "0 failed" in out
    assert not [r for r in fake_api.requests if r[0] == "POST"]


def test_connectors_step_tests_enabled_ones(fake_api_with_github) -> None:
    out = run_demo_check(["--base-url", fake_api_with_github.url])
    assert "connectors: github test ok (exiqo-bot)" in out
    assert ("POST", "/connectors/github/test") in fake_api_with_github.requests


def test_a_failed_connection_test_fails_the_row(fake_api_with_github) -> None:
    fake_api_with_github.tests["github"] = {"ok": False, "message": "host github.x is not allowed"}
    out = run_demo_check(["--base-url", fake_api_with_github.url])
    assert "FAILED: host github.x is not allowed" in out
    assert "1 failed" in out


def test_a_failed_test_without_a_body_is_rendered_readably(fake_api_with_github) -> None:
    fake_api_with_github.tests.pop("github")  # the fake answers its no-test body with 200 ok false
    out = run_demo_check(["--base-url", fake_api_with_github.url])
    assert "FAILED: no test" in out
