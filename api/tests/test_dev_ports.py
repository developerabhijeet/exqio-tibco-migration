"""The dev ports are configured in one place; these tests fail when a copy drifts.

The web calls the API from the browser, so the port the Makefile starts the API on and the port the
web calls have to agree. They agree because both read API_PORT/WEB_PORT from .env
(``web/scripts/root-env.ts`` for the web, the ``dotenv`` function for the Makefile) and neither
carries a port of its own anywhere else. What a reader cannot see from one file is whether the two
fallbacks still match, or whether someone has reintroduced a literal; that is what these check.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"


def _makefile_fallback(key: str) -> str:
    """The port the Makefile uses when .env says nothing: ``KEY ?= $(or $(call dotenv,KEY),9000)``."""
    text = (REPO / "Makefile").read_text()
    match = re.search(rf"^{key} \?= \$\(or \$\(call dotenv,{key}\),(\d+)\)$", text, re.MULTILINE)
    assert match, f"{key} is no longer read from .env with a fallback in the Makefile"
    return match.group(1)


def _web_fallback(key: str) -> str:
    text = (WEB / "scripts" / "root-env.ts").read_text()
    match = re.search(rf'export const DEFAULT_{key} = "(\d+)";', text)
    assert match, f"DEFAULT_{key} is gone from web/scripts/root-env.ts"
    return match.group(1)


def _env_example(key: str) -> str:
    for line in (REPO / ".env.example").read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip()
    raise AssertionError(f"{key} is not in .env.example")


def test_api_port_fallbacks_agree() -> None:
    assert _makefile_fallback("API_PORT") == _web_fallback("API_PORT") == _env_example("API_PORT")


def test_web_port_fallbacks_agree() -> None:
    assert _makefile_fallback("WEB_PORT") == _web_fallback("WEB_PORT") == _env_example("WEB_PORT")


def test_no_api_port_literals_outside_the_one_place() -> None:
    """A hardcoded http://localhost:<port> in the web or the scripts is how the two ends drifted.

    Only HTTP URLs are checked. A database URL carries its own port (the Postgres one, which the
    web never calls) and prose ports in the README and docs are read by people, not by a process,
    so neither can point the web at the wrong backend.
    """
    # Two ports, two single sources. root-env.ts resolves this platform's API port from the
    # repo-root .env. generatedService.ts holds the port the *generated* .NET service listens on in
    # an exported bundle, which is fixed by the artifacts the engine writes rather than by this
    # app's configuration; the test below holds it to the generator's own value.
    allowed = {WEB / "scripts" / "root-env.ts", WEB / "lib" / "generatedService.ts"}
    searched = [
        *(WEB / "lib").rglob("*.ts"),
        *(WEB / "app").rglob("*.ts*"),
        *(WEB / "components").rglob("*.ts*"),
        *(WEB / "e2e").rglob("*.ts"),
        WEB / "next.config.ts",
        WEB / "playwright.config.ts",
        *(REPO / "scripts").glob("*.py"),
    ]
    offenders = [
        f"{path.relative_to(REPO)}: {hit}"
        for path in searched
        if path.is_file() and path not in allowed
        for hit in re.findall(r"https?://localhost:\d+", path.read_text())
    ]
    assert not offenders, (
        "an http://localhost port is hardcoded outside web/scripts/root-env.ts; resolve it "
        "from there instead, so the web cannot point at a port the API has moved off: " + "; ".join(offenders)
    )


def test_web_build_carries_no_fallback_api_url() -> None:
    """The client must not invent an API URL: next.config.ts injects the resolved one, and a build
    made without it fails every call by name (``requireApiUrl``) instead of calling the web origin
    or whatever else answers on a guessed port."""
    api_ts = (WEB / "lib" / "api.ts").read_text()
    assert 'process.env.NEXT_PUBLIC_API_URL ?? ""' in api_ts, "lib/api.ts has a fallback API URL again"
    assert "export function requireApiUrl()" in api_ts, "the missing-URL guard is gone from lib/api.ts"
    assert "NEXT_PUBLIC_API_URL: apiUrl" in (WEB / "next.config.ts").read_text(), (
        "next.config.ts no longer bakes the resolved API URL into the bundle"
    )


def test_the_generated_service_port_agrees_with_the_infrastructure_generator() -> None:
    """The Export panel tells a reviewer which address to open after running the bundle. That port
    is decided by the infrastructure generator (the compose mapping, the Dockerfile, run-local.sh),
    so a change there has to reach the screen, or the panel sends people to a dead port."""
    from engine.infra_gen.generator import TARGET_PORT

    text = (WEB / "lib" / "generatedService.ts").read_text()
    match = re.search(r"GENERATED_SERVICE_PORT\s*=\s*(\d+)", text)
    assert match, "generatedService.ts no longer declares GENERATED_SERVICE_PORT"
    assert int(match.group(1)) == TARGET_PORT
