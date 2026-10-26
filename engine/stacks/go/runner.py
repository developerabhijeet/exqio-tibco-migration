"""``GoRunner``: restore, build, format, test and run a generated Go project (spec section 1).

Every step runs with ``GOFLAGS=-mod=readonly``, ``GOSUMDB=off``, ``GOTOOLCHAIN=local`` and
``GOTELEMETRY=off``: the checksums are the generated ``go.sum`` (ExiQO's pinned lock), the module
proxy is the platform's ``GOPROXY`` (GMF's mirror; ``off`` when the chart has none), and no step
downloads a toolchain or reaches ``sum.golang.org``.

* restore: ``go mod download``;
* build: ``go build -o .exiqo-bin/server ./cmd/server`` then ``go vet ./...``; the
  ``<file>:<line>:<col>: <msg>`` diagnostics of either are the build errors;
* format: ``gofmt -w .`` / ``gofmt -l .`` (any file listed fails the check, the files named);
* test: ``go test -json -coverpkg=./internal/... -coverprofile=.exiqo-bin/cover.out ./...``; the
  test2json stream is joined to the test index (``.exiqo/test-index.json``);
* run: ``.exiqo-bin/server`` with ``PORT=<p>`` and ``HOST=127.0.0.1``, then ``GET /health``.

The child environment starts from ``engine.child_env`` (the API's secrets are not inherited), adds
the Go settings above and the platform's module cache, build cache and proxy, then the runner's
``env`` and the step's own.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from engine.child_env import child_env, run_captured
from engine.dotnet_gen.runner import (
    DEFAULT_TIMEOUT_S,
    BuildResult,
    ServiceHandle,
    StepResult,
    TestRun,
    _tail,
    _toolchain_detect,
)
from engine.stacks.coverage import parse_go_cover
from engine.stacks.results import parse_test2json
from engine.stacks.testindex import join, read_index

HEALTH = "/health"  # the runtime contract's health endpoint
BINARY = ".exiqo-bin/server"
COVER = ".exiqo-bin/cover.out"
TEST_JSON = ".exiqo-bin/test.json"
#: The Go settings every step runs with (global constraint).
GO_SETTINGS = {"GOFLAGS": "-mod=readonly", "GOSUMDB": "off", "GOTOOLCHAIN": "local", "GOTELEMETRY": "off",
               "CGO_ENABLED": "0"}
# What the platform's environment may set for a build: the module proxy (GMF's mirror) and the
# caches the image points at /cache/stacks.
_PLATFORM_KEPT = ("GOPROXY", "GONOPROXY", "GOPRIVATE", "GOMODCACHE", "GOCACHE", "GOPATH", "GOROOT")
_DIAG = re.compile(r"^(?:\./)?(?P<file>[^\s:#][^:]*\.go):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<msg>.*?)\s*$")


def parse_go_errors(log: str, root: Path | None = None) -> tuple[int, list[str]]:
    """``(error count, unique error lines)`` of the compiler and vet diagnostics in a Go log, each
    as ``<file relative to the project>:<line>:<col>: <message>``."""
    prefix = (str(root).replace("\\", "/").rstrip("/") + "/") if root is not None else None
    out: list[str] = []
    for line in log.splitlines():
        m = _DIAG.match(line.strip())
        if not m:
            continue
        file = m.group("file").replace("\\", "/")
        if prefix and file.startswith(prefix):
            file = file[len(prefix):]
        text = f"{file}:{m.group('line')}:{m.group('col') or '1'}: {m.group('msg')}"
        if text not in out:
            out.append(text)
    return len(out), out


def find_tool(name: str) -> str | None:
    """Path of ``go`` (or ``gofmt``, next to it) per ``scripts/toolchain.detect()``, else ``PATH``."""
    try:
        info = _toolchain_detect().get("go") or {}
    except (OSError, ImportError):
        info = {}
    go = info.get("path") or shutil.which("go")
    if name == "go":
        return go
    if go:
        sibling = Path(os.path.realpath(go)).parent / name
        if sibling.is_file():
            return str(sibling)
    return shutil.which(name)


def _count_go(root: Path) -> int:
    return sum(1 for p in root.rglob("*.go") if ".exiqo-bin" not in p.relative_to(root).parts)


class GoRunner:
    def __init__(self, root: Path, go: str | None = None, gofmt: str | None = None,
                 env: dict[str, str] | None = None, coverage_scope: set[str] | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S, api_entry: str | None = None) -> None:
        """``env`` is passed to every step (``TEST_DATABASE_URL`` for the generated tests);
        ``coverage_scope`` are the source files of the process being built (line coverage is
        measured over them); ``api_entry`` is the binary ``run_service`` starts (``.exiqo-bin/server``)."""
        self.root = Path(root).resolve()
        self.go = go or find_tool("go") or "go"
        self.gofmt = gofmt or (str(Path(self.go).parent / "gofmt") if Path(self.go).is_absolute() else None) \
            or find_tool("gofmt") or "gofmt"
        self.timeout_s = timeout_s or DEFAULT_TIMEOUT_S
        self.extra_env: dict[str, str] = dict(env or {})
        self.coverage_scope: set[str] | None = set(coverage_scope) if coverage_scope else None
        self.api_entry = api_entry or BINARY

    # environment
    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = child_env()
        env.update({k: os.environ[k] for k in _PLATFORM_KEPT if os.environ.get(k)})
        env.update(GO_SETTINGS)
        if Path(self.go).is_absolute():
            env["PATH"] = os.pathsep.join([str(Path(self.go).parent), env.get("PATH", "")])
        env.update(self.extra_env)
        if extra:
            env.update(extra)
        return env

    def _run(self, cmd: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
        try:
            code, out, timed_out = run_captured(cmd, cwd=self.root, env=self.env(env), timeout_s=self.timeout_s)
        except FileNotFoundError as exc:
            return 127, f"{cmd[0]} not found: {exc}"
        if timed_out:
            return 124, f"{out}\ntimed out after {self.timeout_s}s: {' '.join(cmd)}"
        return code, out

    # steps
    def restore(self, env: dict[str, str] | None = None) -> StepResult:
        code, log = self._run([self.go, "mod", "download"], env)
        return StepResult(ok=code == 0, log=log)

    def build(self) -> BuildResult:
        (self.root / ".exiqo-bin").mkdir(exist_ok=True)
        code, log = self._run([self.go, "build", "-o", BINARY, "./cmd/server"])
        if code == 0:
            code, vet = self._run([self.go, "vet", "./..."])
            log = f"{log}\n{vet}" if log else vet
        _, errors = parse_go_errors(log, self.root)
        if code != 0 and not errors:
            errors = [ln.strip() for ln in log.splitlines() if ln.strip() and not ln.startswith("#")][:5] \
                or [f"go build failed with exit code {code}"]
        return BuildResult(ok=code == 0, warnings=0, errors=errors, log=log, files=_count_go(self.root))

    def format_write(self, whitespace_only: bool = False) -> StepResult:
        """``gofmt -w .``; ``whitespace_only`` has no gofmt equivalent."""
        code, log = self._run([self.gofmt, "-w", "."])
        return StepResult(ok=code == 0, log=log)

    def format_check(self, whitespace_only: bool = False) -> StepResult:
        """``gofmt -l .``: the files it lists are not gofmt-formatted, and the check fails naming them."""
        code, log = self._run([self.gofmt, "-l", "."])
        listed = [ln.strip() for ln in log.splitlines() if ln.strip().endswith(".go")]
        if code == 0 and listed:
            return StepResult(ok=False, log="not gofmt-formatted (gofmt -l):\n" + "\n".join(listed))
        return StepResult(ok=code == 0, log=log)

    def test(self) -> TestRun:
        out = self.root / ".exiqo-bin"
        out.mkdir(exist_ok=True)
        cover, stream = self.root / COVER, self.root / TEST_JSON
        cover.unlink(missing_ok=True)
        stream.unlink(missing_ok=True)
        code, log = self._run([self.go, "test", "-json", "-coverpkg=./internal/...", f"-coverprofile={COVER}", "./..."])
        stream.write_text(log, encoding="utf-8")
        cases = join(parse_test2json(stream), read_index(self.root))
        coverage = parse_go_cover(cover, self.coverage_scope) if cover.is_file() else None
        ok = code == 0 and bool(cases) and all(c.outcome != "failed" for c in cases)
        return TestRun(ok=ok, cases=cases, coverage_line=coverage, trx_path=str(stream), log=log)

    # service
    def binary(self) -> Path:
        return self.root / self.api_entry

    def run_service(self, port: int, env: dict[str, str] | None = None, timeout_s: float = 180.0) -> ServiceHandle:
        """Start ``.exiqo-bin/server`` on ``http://127.0.0.1:<port>`` and wait until ``GET /health``
        is 200 (``build`` writes the binary).

        Output goes to ``<root>/.exiqo/logs/server-<port>.log``; ``RuntimeError`` with the log
        tail when the process exits or the health check does not pass in time."""
        binary = self.binary()
        if not binary.is_file():
            raise FileNotFoundError(f"{binary} is missing: build the project first")
        url = f"http://127.0.0.1:{port}"
        logs = self.root / ".exiqo" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"server-{port}.log"
        out = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - handle owned by the child
        proc = subprocess.Popen(
            [str(binary)], cwd=self.root, env=self.env({**(env or {}), "PORT": str(port), "HOST": "127.0.0.1"}),
            stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        out.close()
        handle = ServiceHandle(pid=proc.pid, url=url, log_path=str(log_path), process=proc)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"service exited with code {proc.returncode}:\n{_tail(log_path)}")
            try:
                with urllib.request.urlopen(url + HEALTH, timeout=2) as resp:
                    if resp.status == 200:
                        return handle
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.3)
        handle.stop()
        raise RuntimeError(f"service did not answer {HEALTH} within {timeout_s}s:\n{_tail(log_path)}")


__all__ = ["GO_SETTINGS", "GoRunner", "find_tool", "parse_go_errors"]
