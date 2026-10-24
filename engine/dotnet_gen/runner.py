"""DotnetRunner: restore, build, format, test and run a generated solution (design spec 2.5, 2.6).

Plain code decides compile and test results: the runner shells out to the ``dotnet`` CLI, parses
``error CS####`` lines and the warning count, the TRX results and the Cobertura report, and maps
each test back to a rule, branch or scenario id by the naming convention in ``trx``.

The binary comes from ``scripts/toolchain.detect()`` (``<repo>/.tools/bin``, ``.tools/dotnet``,
then ``PATH``). When ``<repo>/.tools/nuget-cache`` exists (``make nuget-cache``) its path is passed
as ``EXIQO_NUGET_CACHE``; the generated ``Directory.Build.props`` adds it as a restore source, so a
machine without access to api.nuget.org can restore from the offline cache.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from engine.child_env import child_env
from engine.dotnet_gen.coverage import merge_cobertura
from engine.dotnet_gen.trx import TestCase, parse_trx

REPO_ROOT = Path(__file__).resolve().parents[2]
NUGET_CACHE = REPO_ROOT / ".tools" / "nuget-cache"
DEFAULT_TIMEOUT_S = 1800

_DIAG = re.compile(r"^(?P<loc>.*?)\s*:\s*(?P<kind>error|warning)\s+(?P<code>[A-Z]+\d+)\s*:\s*(?P<msg>.*?)"
                   r"(?:\s+\[[^\]]*\])?\s*$")
_WARN_SUMMARY = re.compile(r"^\s*(\d+)\s+Warning\(s\)\s*$", re.MULTILINE)
_NUGET_DOWN = re.compile(r"NU1301|NU1101|NU1102|Unable to load the service index|"
                         r"unable to find package|api\.nuget\.org", re.IGNORECASE)


class StepResult(BaseModel):
    ok: bool
    log: str = ""


class BuildResult(BaseModel):
    ok: bool
    warnings: int = 0
    errors: list[str] = Field(default_factory=list)
    log: str = ""
    files: int = 0  # C# files in the solution (standards adherence denominator)


class TestRun(BaseModel):
    __test__ = False  # not a pytest test class

    ok: bool
    cases: list[TestCase] = Field(default_factory=list)
    coverage_line: float | None = None
    trx_path: str | None = None
    log: str = ""


@dataclass
class ServiceHandle:
    pid: int
    url: str
    log_path: str
    process: subprocess.Popen = field(repr=False)

    def poll(self) -> int | None:
        return self.process.poll()

    def stop(self, timeout_s: float = 15.0) -> None:
        """Terminate the process group (``dotnet run`` and the app it started)."""
        if self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            self.process.terminate()
        try:
            self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self.process.kill()
            self.process.wait(timeout=timeout_s)


# ------------------------------------------------------------------ helpers
def _toolchain_detect() -> dict:
    path = REPO_ROOT / "scripts" / "toolchain.py"
    spec = importlib.util.spec_from_file_location("exiqo_toolchain", path)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.detect()


def find_dotnet() -> str | None:
    """Path of the ``dotnet`` binary per ``scripts/toolchain.detect()``, or ``None``."""
    try:
        info = _toolchain_detect().get("dotnet") or {}
    except (OSError, ImportError):
        info = {}
    return info.get("path") or shutil.which("dotnet")


def parse_build_log(log: str) -> tuple[int, list[str]]:
    """``(warning count, unique error lines)`` from ``dotnet build`` output.

    MSBuild prints each diagnostic twice (during the build and in the summary), so diagnostics are
    de-duplicated; the ``N Warning(s)`` summary line wins when present. The trailing ``[project]``
    is dropped from error lines.
    """
    errors: list[str] = []
    warnings: set[str] = set()
    for line in log.splitlines():
        m = _DIAG.match(line.strip())
        if not m:
            continue
        text = f"{m.group('loc')}: {m.group('kind')} {m.group('code')}: {m.group('msg')}".strip()
        if m.group("kind") == "error":
            if text not in errors:
                errors.append(text)
        else:
            warnings.add(text)
    summary = _WARN_SUMMARY.findall(log)
    count = int(summary[-1]) if summary else len(warnings)
    return count, errors


def nuget_unreachable(log: str) -> bool:
    """True when a restore failed because the package source could not be reached."""
    return bool(_NUGET_DOWN.search(log or ""))


def nuget_available(timeout_s: float = 5.0) -> bool:
    """True when packages can be restored: the offline cache exists or api.nuget.org answers.

    A cheap probe so callers can skip a restore that would spend a minute retrying an unreachable
    source; ``restore()`` itself still reports the real outcome.
    """
    if os.environ.get("EXIQO_NUGET_CACHE") or NUGET_CACHE.is_dir():
        return True
    try:
        with urllib.request.urlopen("https://api.nuget.org/v3/index.json", timeout=timeout_s) as resp:
            return resp.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def _count_cs(root: Path) -> int:
    return sum(1 for p in root.rglob("*.cs") if not ({"bin", "obj"} & set(p.relative_to(root).parts)))


# ------------------------------------------------------------------- runner
class DotnetRunner:
    def __init__(self, root: Path, dotnet: str | None = None, timeout_s: int = DEFAULT_TIMEOUT_S,
                 env: dict[str, str] | None = None, coverage_scope: set[str] | None = None) -> None:
        """``env`` is passed to every step this runner starts (for example ``TEST_DATABASE_URL``
        for the generated tests); a step's own ``env=`` is applied on top of it.

        ``coverage_scope`` are the source files generated for the process being built; line
        coverage is then measured over those files rather than the whole solution. It belongs to
        the runner rather than to one call so the repair loop reports the same number."""
        self.root = Path(root).resolve()
        self.dotnet = dotnet or find_dotnet() or "dotnet"
        self.timeout_s = timeout_s
        self.extra_env: dict[str, str] = dict(env or {})
        self.coverage_scope: set[str] | None = set(coverage_scope) if coverage_scope else None

    # environment
    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The child environment: the allowlist from ``engine.child_env`` (the API's secrets are
        not inherited), the dotnet CLI settings, the runner's ``env`` and then ``extra``."""
        env = child_env()
        env.update({"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1",
                    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "MSBUILDDISABLENODEREUSE": "1",
                    "DOTNET_CLI_UI_LANGUAGE": "en"})
        resolved = Path(self.dotnet)
        if resolved.is_absolute():
            # the runtime next to the binary (a local .tools/dotnet install is not on the loader path)
            env["DOTNET_ROOT"] = str(Path(os.path.realpath(resolved)).parent)
            env["PATH"] = f"{resolved.parent}{os.pathsep}{env.get('PATH', '')}"
        if not env.get("EXIQO_NUGET_CACHE") and NUGET_CACHE.is_dir():
            env["EXIQO_NUGET_CACHE"] = str(NUGET_CACHE)
        env.update(self.extra_env)
        if extra:
            env.update(extra)
        return env

    def solution(self) -> Path:
        slns = sorted(self.root.glob("*.sln")) + sorted(self.root.glob("*.slnx"))
        if not slns:
            raise FileNotFoundError(f"no .sln in {self.root}")
        return slns[0]

    def _target(self, project: str | None) -> str:
        return str(self.root / project) if project else str(self.solution())

    def _run(self, args: list[str], timeout_s: int | None = None,
             env: dict[str, str] | None = None) -> tuple[int, str]:
        try:
            proc = subprocess.run([self.dotnet, *args], cwd=self.root, env=self.env(env), capture_output=True,
                                  text=True, timeout=timeout_s or self.timeout_s, check=False)
        except FileNotFoundError as exc:
            return 127, f"dotnet not found: {exc}"
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return 124, f"{out}\ntimed out after {exc.timeout}s: dotnet {' '.join(args)}"
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    # steps
    def restore(self, project: str | None = None, env: dict[str, str] | None = None) -> StepResult:
        code, log = self._run(["restore", self._target(project)], env=env)
        return StepResult(ok=code == 0, log=log)

    def build(self, project: str | None = None, configuration: str = "Debug",
              env: dict[str, str] | None = None) -> BuildResult:
        code, log = self._run(["build", self._target(project), "-c", configuration, "-nologo"], env=env)
        warnings, errors = parse_build_log(log)
        return BuildResult(ok=code == 0, warnings=warnings, errors=errors, log=log, files=_count_cs(self.root))

    # Only the whitespace pass, never the analyzer pass. `dotnet format` with no subcommand also
    # applies analyzer code fixes, and the xUnit analyzers carry one (xUnit1013, "public method in a
    # test class should be a test") that writes a [Theory] attribute onto any public helper in a test
    # class. The scenario tests have exactly such a helper, RunAsync; a [Theory] with no InlineData
    # is error xUnit1003, so letting the formatter rewrite code turned a tidy-up into a build break.
    # Layout is the formatter's job here; what the code says is the generator's and the agents'.
    def _format_args(self, whitespace_only: bool, verify: bool) -> list[str]:
        target = [str(self.root), "--folder"] if whitespace_only else [str(self.solution())]
        return ["format", "whitespace", *target, *(["--verify-no-changes"] if verify else []), "-v", "q"]

    def format_write(self, whitespace_only: bool = False, env: dict[str, str] | None = None) -> StepResult:
        """``dotnet format whitespace`` in write mode: lay the generated code out properly.

        The generator emits C# by template and the model fills method bodies, so neither arrives
        laid out the way the formatter would lay it out. Formatting first makes the check that
        follows a real check rather than a foregone failure."""
        code, log = self._run(self._format_args(whitespace_only, verify=False), env=env)
        return StepResult(ok=code == 0, log=log)

    def format_check(self, whitespace_only: bool = False, env: dict[str, str] | None = None) -> StepResult:
        """``dotnet format whitespace --verify-no-changes``; ``whitespace_only`` uses folder mode,
        which needs no restore (usable where NuGet is unreachable)."""
        code, log = self._run(self._format_args(whitespace_only, verify=True), env=env)
        return StepResult(ok=code == 0, log=log)

    def test(self, project: str | None = None, env: dict[str, str] | None = None) -> TestRun:
        results = self.root / "TestResults"
        shutil.rmtree(results, ignore_errors=True)
        args = ["test", self._target(project), "--logger", "trx;LogFileName=results.trx",
                "--collect:XPlat Code Coverage", "--results-directory", str(results), "-nologo"]
        code, log = self._run(args, env=env)
        trx_files = sorted(results.rglob("*.trx"), key=lambda p: p.stat().st_mtime) if results.exists() else []
        cases: list[TestCase] = []
        for f in trx_files:
            cases += parse_trx(f)
        cov = sorted(results.rglob("coverage.cobertura.xml")) if results.exists() else []
        coverage_line = merge_cobertura(list(cov), self.coverage_scope) if cov else None
        ok = code == 0 and bool(cases) and all(c.outcome != "failed" for c in cases)
        return TestRun(ok=ok, cases=cases, coverage_line=coverage_line,
                       trx_path=str(trx_files[-1]) if trx_files else None, log=log)

    # service
    def _project_file(self, project: str) -> Path:
        cand = self.root / project
        if cand.is_file():
            return cand
        if cand.is_dir():
            found = sorted(cand.glob("*.csproj"))
            if found:
                return found[0]
        name = Path(project).name.removesuffix(".csproj")
        hits = sorted(p for p in self.root.rglob(f"{name}.csproj")
                      if not ({"bin", "obj"} & set(p.relative_to(self.root).parts)))
        if not hits:
            raise FileNotFoundError(f"project {project} not found under {self.root}")
        return hits[0]

    def run_service(self, project: str, port: int, env: dict[str, str] | None = None,
                    timeout_s: float = 180.0, health_path: str = "/health") -> ServiceHandle:
        """Start ``dotnet run`` on ``http://127.0.0.1:<port>`` and wait until ``GET /health`` is 200.

        Output goes to ``<root>/.exiqo/logs/<project>-<port>.log``. Raises ``RuntimeError`` with the
        log tail when the process exits or the health check does not pass in time.
        """
        csproj = self._project_file(project)
        url = f"http://127.0.0.1:{port}"
        logs = self.root / ".exiqo" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"{csproj.stem}-{port}.log"
        out = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - handle owned by the child
        child_env = self.env({"ASPNETCORE_URLS": url, **(env or {})})
        proc = subprocess.Popen(
            [self.dotnet, "run", "--project", str(csproj), "--no-launch-profile", "--urls", url],
            cwd=self.root, env=child_env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        out.close()
        handle = ServiceHandle(pid=proc.pid, url=url, log_path=str(log_path), process=proc)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"service exited with code {proc.returncode}:\n{_tail(log_path)}")
            try:
                with urllib.request.urlopen(url + health_path, timeout=2) as resp:
                    if resp.status == 200:
                        return handle
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        handle.stop()
        raise RuntimeError(f"service did not answer {health_path} within {timeout_s}s:\n{_tail(log_path)}")


def _tail(path: Path, n: int = 60) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""
