"""``UvRunner``: restore, build, format, test and run a generated Python project (spec section 1).

* restore: ``uv venv .venv --python 3.12`` (an installed interpreter of the pinned line, never a
  download: ``UV_PYTHON_DOWNLOADS=never``), then ``uv pip install --require-hashes --no-build -r
  requirements.lock`` from GMF's mirror (``UV_INDEX_URL``, passed only when set); nothing is resolved
  or built, every file's hash is checked, and a venv off the 3.12 line fails the step. Every later
  step runs from ``.venv`` with no network;
* build: ``python -m compileall -q src`` and ``ruff check --select E9,F63,F7,F82 src tests``; the
  compileall ``File "<f>", line <l>`` and ruff ``<f>:<l>:<c>: <code> <msg>`` diagnostics are the
  build errors;
* format: ``ruff format`` / ``ruff format --check``. The generator and the agents do not write
  ruff's layout, so the format gate is always write-then-check;
* test: ``pytest --junitxml=.exiqo-bin/junit.xml --cov=src --cov-report=xml:.exiqo-bin/coverage.xml``;
  the JUnit XML is joined to the test index (``.exiqo/test-index.json``);
* run: ``.venv/bin/uvicorn --factory <pkg>.main:create_app --app-dir src`` with ``PORT``.

The child environment starts from ``engine.child_env`` (the API's secrets are not inherited), adds
uv's own settings from the platform (``UV_CACHE_DIR``, ``UV_PYTHON_DOWNLOADS``, ``UV_NO_PROGRESS``,
``UV_INDEX_URL`` when set; the mirror CA as ``SSL_CERT_FILE``), ``VIRTUAL_ENV`` and ``PATH`` with
``.venv/bin`` first, then the runner's ``env`` and the step's own.
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
from engine.stacks.coverage import parse_cobertura_xml
from engine.stacks.python import versions
from engine.stacks.results import parse_junit
from engine.stacks.testindex import join, read_index

HEALTH = "/health"  # the runtime contract's health endpoint
VENV = ".venv"
OUT = ".exiqo-bin"
LINT_RULES = "E9,F63,F7,F82"  # syntax errors, bad comparisons, misplaced statements, undefined names
_UV_SETTINGS = ("UV_CACHE_DIR", "UV_PYTHON_DOWNLOADS", "UV_NO_PROGRESS", "UV_INDEX_URL", "UV_NATIVE_TLS")
_COMPILE_FILE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')
_COMPILE_MSG = re.compile(r"^(?:\w+\.)*\w*(?:Error|Exception)\b:?\s*(?P<msg>.*)$")
_RUFF = re.compile(r"^(?P<file>[^\s:][^:]*\.py):(?P<line>\d+):(?P<col>\d+): (?P<msg>.+)$")


def _relative(path: str, root: Path | None) -> str:
    p = path.replace("\\", "/")
    if root is not None:
        prefix = str(root).replace("\\", "/").rstrip("/") + "/"
        if p.startswith(prefix):
            return p[len(prefix):]
    return p.removeprefix("./")


def parse_python_errors(log: str, root: Path | None = None) -> tuple[int, list[str]]:
    """``(error count, unique error lines)`` of a compileall and a ruff log, each as
    ``<file relative to the project>:<line>[:<col>]: <message>``; a syntax error both tools report
    on the same line counts once (ruff's, which carries the column)."""
    out: dict[tuple[str, str], str] = {}
    lines = log.splitlines()
    for i, line in enumerate(lines):
        m = _RUFF.match(line.strip())
        if m:
            f = _relative(m.group("file"), root)
            out[(f, m.group("line"))] = f"{f}:{m.group('line')}:{m.group('col')}: {m.group('msg')}"
            continue
        m = _COMPILE_FILE.match(line)
        if not m:
            continue
        f = _relative(m.group("file"), root)
        msg = next((mm.group(0).strip() for nxt in lines[i + 1:i + 6] if (mm := _COMPILE_MSG.match(nxt.strip()))),
                   "does not compile")
        out.setdefault((f, m.group("line")), f"{f}:{m.group('line')}: {msg}")
    return len(out), list(out.values())


def find_tool(name: str) -> str | None:
    """Path of ``python3`` or ``uv`` per ``scripts/toolchain.detect()``, else ``PATH``."""
    try:
        info = _toolchain_detect().get(name) or {}
    except (OSError, ImportError):
        info = {}
    return info.get("path") or shutil.which(name)


def _count_python(root: Path) -> int:
    return sum(1 for folder in ("src", "tests") if (root / folder).is_dir()
               for p in (root / folder).rglob("*.py") if "__pycache__" not in p.parts)


class UvRunner:
    def __init__(self, root: Path, python: str | None = None, uv: str | None = None,
                 env: dict[str, str] | None = None, coverage_scope: set[str] | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S, api_entry: str | None = None) -> None:
        """``python`` is the interpreter the venv is made from (a path, or by default the pinned line,
        ``3.12``, which uv finds among the installed interpreters; restore refuses any other line);
        ``env`` is passed to every step (``TEST_DATABASE_URL`` for the generated tests);
        ``coverage_scope`` are the source files of the process being built (line coverage is
        measured over them); ``api_entry`` is the app factory ``run_service`` starts
        (``<pkg>.main:create_app``, the single package under ``src`` when omitted)."""
        self.root = Path(root).resolve()
        # uv finds an installed interpreter of the pinned line (UV_PYTHON_DOWNLOADS=never: never a download)
        self.python = python or versions.PYTHON
        self.uv = uv or find_tool("uv") or "uv"
        self.timeout_s = timeout_s or DEFAULT_TIMEOUT_S
        self.extra_env: dict[str, str] = dict(env or {})
        self.coverage_scope: set[str] | None = set(coverage_scope) if coverage_scope else None
        self.api_entry = api_entry

    @property
    def venv(self) -> Path:
        return self.root / VENV

    def tool(self, name: str) -> str:
        """A tool of the project's virtual environment (``python``, ``ruff``, ``uvicorn``)."""
        return str(self.venv / ("Scripts" if os.name == "nt" else "bin") / name)

    # environment
    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = child_env()
        for name in _UV_SETTINGS:
            value = os.environ.get(name, "")
            if value:
                env[name] = value
        env.setdefault("UV_PYTHON_DOWNLOADS", "never")
        ca = os.environ.get("MIRROR_CA_BUNDLE", "")
        if ca and Path(ca).is_file() and "SSL_CERT_FILE" not in env:
            env["SSL_CERT_FILE"] = ca
        env["VIRTUAL_ENV"] = str(self.venv)
        dirs = [str(Path(self.tool("python")).parent)]
        dirs += [str(Path(t).parent) for t in (self.uv,) if Path(t).is_absolute()]
        env["PATH"] = os.pathsep.join([*dict.fromkeys(dirs), env.get("PATH", "")])
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
        code, log = self._run([self.uv, "venv", VENV, "--python", self.python, "--allow-existing"], env)
        if code != 0:
            return StepResult(ok=False, log=log)
        code, more = self._run([self.uv, "pip", "install", "--require-hashes", "--no-build", "-r",
                                "requirements.lock", "--python", self.tool("python")], env)
        log = f"{log}\n{more}"
        if code != 0:
            return StepResult(ok=False, log=log)
        line = tuple(int(p) for p in versions.PYTHON.split("."))
        code, _ = self._run([self.tool("python"), "-c", f"import sys; sys.exit(sys.version_info[:2] != {line})"], env)
        if code != 0:
            return StepResult(ok=False, log=f"{log}\nthe venv's interpreter is not Python {versions.PYTHON} "
                                            f"({self.python}): the generated project is pinned to that line")
        return StepResult(ok=True, log=log)

    def build(self) -> BuildResult:
        c1, log1 = self._run([self.tool("python"), "-m", "compileall", "-q", "src"])
        folders = [f for f in ("src", "tests") if (self.root / f).is_dir()]
        c2, log2 = self._run([self.tool("ruff"), "check", "--no-cache", "--select", LINT_RULES,
                              "--output-format", "concise", *folders])
        log = f"{log1}\n{log2}"
        _, errors = parse_python_errors(log, self.root)
        ok = c1 == 0 and c2 == 0
        if not ok and not errors:
            errors = [ln.strip() for ln in log.splitlines() if ln.strip()][:5] or [f"build failed ({c1}, {c2})"]
        return BuildResult(ok=ok, warnings=0, errors=errors, log=log, files=_count_python(self.root))

    def format_write(self, whitespace_only: bool = False) -> StepResult:
        """``ruff format``; ``whitespace_only`` has no ruff equivalent."""
        code, log = self._run([self.tool("ruff"), "format", "--no-cache", "src", "tests"])
        return StepResult(ok=code == 0, log=log)

    def format_check(self, whitespace_only: bool = False) -> StepResult:
        code, log = self._run([self.tool("ruff"), "format", "--no-cache", "--check", "src", "tests"])
        return StepResult(ok=code == 0, log=log)

    def test(self) -> TestRun:
        out = self.root / OUT
        junit, coverage_xml = out / "junit.xml", out / "coverage.xml"
        out.mkdir(parents=True, exist_ok=True)
        junit.unlink(missing_ok=True)
        coverage_xml.unlink(missing_ok=True)
        code, log = self._run([self.tool("python"), "-m", "pytest", f"--junitxml={OUT}/junit.xml", "--cov=src",
                               f"--cov-report=xml:{OUT}/coverage.xml", "-q"])
        cases = join(parse_junit([junit]), read_index(self.root)) if junit.is_file() else []
        coverage = parse_cobertura_xml([coverage_xml], self.coverage_scope) if coverage_xml.is_file() else None
        ok = code == 0 and bool(cases) and all(c.outcome != "failed" for c in cases)
        return TestRun(ok=ok, cases=cases, coverage_line=coverage, trx_path=str(junit) if junit.is_file() else None,
                       log=log)

    # service
    def app(self) -> str:
        if self.api_entry:
            return self.api_entry
        hits = sorted(p.parent.name for p in (self.root / "src").glob("*/main.py"))
        if len(hits) != 1:
            raise FileNotFoundError(f"expected one package with main.py under {self.root / 'src'}, found {hits or 'none'}")
        return f"{hits[0]}.main:create_app"

    def run_service(self, port: int, env: dict[str, str] | None = None, timeout_s: float = 180.0) -> ServiceHandle:
        """Start uvicorn on ``http://127.0.0.1:<port>`` and wait until ``GET /health`` is 200.

        Output goes to ``<root>/.exiqo/logs/service-<port>.log``; ``RuntimeError`` with the log
        tail when the process exits or the health check does not pass in time."""
        app = self.app()
        url = f"http://127.0.0.1:{port}"
        logs = self.root / ".exiqo" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"service-{port}.log"
        out = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - handle owned by the child
        cmd = [self.tool("uvicorn"), "--factory", app, "--app-dir", str(self.root / "src"),
               "--host", "127.0.0.1", "--port", str(port)]
        proc = subprocess.Popen(cmd, cwd=self.root, env=self.env({**(env or {}), "PORT": str(port)}), stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
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
            time.sleep(0.5)
        handle.stop()
        raise RuntimeError(f"service did not answer {HEALTH} within {timeout_s}s:\n{_tail(log_path)}")


__all__ = ["UvRunner", "find_tool", "parse_python_errors"]
