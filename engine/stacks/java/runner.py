"""``MavenRunner``: restore, build, format, test and run a generated Java project (spec section 1).

Every step runs ``mvn -B -ntp -s .exiqo/maven-settings.xml``; the settings file carries the GMF
mirror (``MAVEN_MIRROR_URL``) as the only repository, and every step after ``restore`` is offline
(``-o``), so a build never resolves anything restore did not fetch.

* restore: ``dependency:go-offline`` plus ``spotless:apply`` on no file, which resolves the
  formatter (palantir-java-format) that ``go-offline`` does not fetch, without touching a source;
* build: ``-DskipTests package`` (the jar is kept for Verify); ``[ERROR] <file>:[l,c] <msg>``
  diagnostics are the build errors;
* format: ``spotless:apply`` / ``spotless:check``. The generator and the agents do not write
  palantir layout, so the format gate is always write-then-check;
* test: ``test`` with ``-Dmaven.test.failure.ignore=true`` so a failing test still leaves the
  JaCoCo report; Surefire XML is joined to the test index (``.exiqo/test-index.json``);
* run: ``java -jar target/<artifact>.jar --server.port=<p>``.

The child environment starts from ``engine.child_env`` (the API's secrets are not inherited), adds
``JAVA_HOME``, ``MAVEN_OPTS`` with only the platform's ``-Dmaven.repo.local`` (the worker's shared
cache), the tools' folders on ``PATH``, then the runner's ``env`` and the step's own.
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
from engine.stacks.coverage import parse_jacoco
from engine.stacks.results import parse_junit
from engine.stacks.testindex import join, read_index

SETTINGS = ".exiqo/maven-settings.xml"
HEALTH = "/health"  # the runtime contract's health endpoint
# The platform MAVEN_OPTS tokens a build keeps: the local repository and the mirror CA trust store
# the entrypoint builds (never anything else, so a proxy password cannot leak into generated code).
_MAVEN_OPTS_KEPT = ("-Dmaven.repo.local=", "-Djavax.net.ssl.trustStore=", "-Djavax.net.ssl.trustStorePassword=")
_BASE = ["-B", "-ntp", "-s", SETTINGS]
# go-offline resolves the plugins but not their own dependencies (the formatter, Surefire's JUnit
# Platform provider) and not always the versions the build resolves, so restore also resolves the
# project's dependencies and loads the two plugins without running them: spotless on a file pattern
# no path matches, surefire with its tests skipped.
RESTORE_GOALS = ["dependency:resolve", "dependency:go-offline", "spotless:apply", "-DspotlessFiles=^$",
            "surefire:test", "-DskipTests"]
_DIAG = re.compile(r"^\[(?P<kind>ERROR|WARNING)\]\s+(?P<file>(?:[A-Za-z]:)?[^\s:\[][^:\[]*\.java):\[(?P<line>\d+),(?P<col>\d+)\]\s*(?P<msg>.*?)\s*$")
_DETAIL = re.compile(r"^\s*(?:\[ERROR\]\s*)?(symbol|location)\s*:\s*(.*?)\s*$")
_SRC = re.compile(r"(?:^|/)(src/(?:main|test)/.*)$")
_NOISE = re.compile(r"^\[ERROR\]\s*(?:$|->|To see the full|Re-run Maven|For more information|\[Help|"
                    r"http|After correcting|mvn <args>|COMPILATION ERROR|Failed to execute goal.*compil)")


def _relative(path: str, root: Path | None) -> str:
    p = path.replace("\\", "/")
    if root is not None:
        prefix = str(root).replace("\\", "/").rstrip("/") + "/"
        if p.startswith(prefix):
            return p[len(prefix):]
    m = _SRC.search(p)
    return m.group(1) if m else p


def _diagnostics(log: str, kind: str, root: Path | None) -> list[str]:
    """One line per distinct diagnostic (file, position and message); the copy that carries
    javac's symbol and location lines wins."""
    out: dict[str, str] = {}
    lines = log.splitlines()
    for i, line in enumerate(lines):
        m = _DIAG.match(line.strip())
        if not m or m.group("kind") != kind:
            continue
        text = f"{_relative(m.group('file'), root)}:{m.group('line')}:{m.group('col')}: {m.group('msg')}"
        details = []
        for nxt in lines[i + 1:i + 3]:
            d = _DETAIL.match(nxt)
            if not d:
                break
            details.append(f"{d.group(1)}: {d.group(2)}")
        key = text
        if details:
            text += f" ({'; '.join(details)})"
        if len(text) > len(out.get(key, "")):
            out[key] = text
    return list(out.values())


def parse_maven_errors(log: str, root: Path | None = None) -> tuple[int, list[str]]:
    """``(error count, unique error lines)`` of the javac diagnostics in a Maven log, each as
    ``<file relative to the project>:<line>:<col>: <message>``. Maven prints each diagnostic twice
    (during compilation and in the failure summary); they are de-duplicated."""
    errors = _diagnostics(log, "ERROR", root)
    return len(errors), errors


def maven_warning_count(log: str, root: Path | None = None) -> int:
    """The distinct javac warnings in a Maven log."""
    return len(_diagnostics(log, "WARNING", root))


def _summary_errors(log: str) -> list[str]:
    """The first ``[ERROR]`` lines of a failed run that is not a compile failure (dependency
    resolution, plugin failure), without Maven's help boilerplate."""
    out = []
    for line in log.splitlines():
        s = line.strip()
        if s.startswith("[ERROR]") and not _NOISE.match(s) and s not in out:
            out.append(s)
        if len(out) == 5:
            break
    return out


def find_tool(name: str) -> str | None:
    """Path of ``java`` or ``mvn`` per ``scripts/toolchain.detect()``, else ``PATH``."""
    try:
        info = _toolchain_detect().get(name) or {}
    except (OSError, ImportError):
        info = {}
    return info.get("path") or shutil.which(name)


def _count_java(root: Path) -> int:
    return sum(1 for p in root.rglob("*.java") if "target" not in p.relative_to(root).parts)


class MavenRunner:
    def __init__(self, root: Path, java: str | None = None, mvn: str | None = None,
                 env: dict[str, str] | None = None, coverage_scope: set[str] | None = None,
                 timeout_s: int = DEFAULT_TIMEOUT_S, api_entry: str | None = None) -> None:
        """``env`` is passed to every step (``TEST_DATABASE_URL`` for the generated tests);
        ``coverage_scope`` are the source files of the process being built (line coverage is
        measured over them); ``api_entry`` is the jar ``run_service`` starts (``target/<a>.jar``,
        the single one under ``target`` when omitted)."""
        self.root = Path(root).resolve()
        self.java = java or find_tool("java") or "java"
        self.mvn = mvn or find_tool("mvn") or "mvn"
        self.timeout_s = timeout_s or DEFAULT_TIMEOUT_S
        self.extra_env: dict[str, str] = dict(env or {})
        self.coverage_scope: set[str] | None = set(coverage_scope) if coverage_scope else None
        self.api_entry = api_entry

    # environment
    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = child_env()
        java_home = os.environ.get("JAVA_HOME", "")
        java = Path(self.java)
        if not java_home and java.is_absolute():
            java_home = str(Path(os.path.realpath(java)).parent.parent)
        if java_home:
            env["JAVA_HOME"] = java_home
        repo = [t for t in os.environ.get("MAVEN_OPTS", "").split() if t.startswith(_MAVEN_OPTS_KEPT)]
        env["MAVEN_OPTS"] = " ".join(repo)
        dirs = [str(Path(t).parent) for t in (self.java, self.mvn) if Path(t).is_absolute()]
        if java_home:
            dirs.append(str(Path(java_home) / "bin"))
        if dirs:
            env["PATH"] = os.pathsep.join([*dict.fromkeys(dirs), env.get("PATH", "")])
        env.update(self.extra_env)
        if extra:
            env.update(extra)
        return env

    def _mvn(self, args: list[str], env: dict[str, str] | None = None, offline: bool = True) -> tuple[int, str]:
        cmd = [self.mvn, *_BASE, *(["-o"] if offline else []), *args]
        try:
            code, out, timed_out = run_captured(cmd, cwd=self.root, env=self.env(env), timeout_s=self.timeout_s)
        except FileNotFoundError as exc:
            return 127, f"mvn not found: {exc}"
        if timed_out:
            return 124, f"{out}\ntimed out after {self.timeout_s}s: mvn {' '.join(args)}"
        return code, out

    # steps
    def mirror_problem(self) -> str | None:
        """Why the restore must not run: the project's settings name no mirror, so Maven would use
        Maven Central. The mirror is GMF's (``MAVEN_MIRROR_URL`` when the project was generated)."""
        settings = self.root / SETTINGS
        text = settings.read_text(encoding="utf-8", errors="replace") if settings.is_file() else ""
        if "<mirror>" in text:
            return None
        return ("MAVEN_MIRROR_URL is not set: the project's .exiqo/maven-settings.xml names no mirror, so Maven "
                "would use Maven Central. Set GMF's Maven mirror (mirrors.maven) and generate again.")

    def restore(self, env: dict[str, str] | None = None) -> StepResult:
        problem = self.mirror_problem()
        if problem:
            return StepResult(ok=False, log=problem)
        code, log = self._mvn(RESTORE_GOALS, env=env, offline=False)
        return StepResult(ok=code == 0, log=log)

    def build(self) -> BuildResult:
        code, log = self._mvn(["-DskipTests", "package"])
        _, errors = parse_maven_errors(log, self.root)
        if code != 0 and not errors:
            errors = _summary_errors(log) or [f"mvn package failed with exit code {code}"]
        return BuildResult(ok=code == 0, warnings=maven_warning_count(log, self.root), errors=errors, log=log,
                           files=_count_java(self.root))

    def format_write(self, whitespace_only: bool = False) -> StepResult:
        """``spotless:apply`` (palantir-java-format); ``whitespace_only`` has no Maven equivalent."""
        code, log = self._mvn(["spotless:apply"])
        return StepResult(ok=code == 0, log=log)

    def format_check(self, whitespace_only: bool = False) -> StepResult:
        code, log = self._mvn(["spotless:check"])
        return StepResult(ok=code == 0, log=log)

    def test(self) -> TestRun:
        reports = self.root / "target" / "surefire-reports"
        jacoco = self.root / "target" / "site" / "jacoco" / "jacoco.xml"
        shutil.rmtree(reports, ignore_errors=True)
        jacoco.unlink(missing_ok=True)
        code, log = self._mvn(["-Dmaven.test.failure.ignore=true", "test"])
        files = sorted(reports.glob("TEST-*.xml")) if reports.is_dir() else []
        cases = join(parse_junit(files), read_index(self.root)) if files else []
        coverage = parse_jacoco(jacoco, self.coverage_scope) if jacoco.is_file() else None
        ok = code == 0 and bool(cases) and all(c.outcome != "failed" for c in cases)
        return TestRun(ok=ok, cases=cases, coverage_line=coverage,
                       trx_path=str(files[-1]) if files else None, log=log)

    # service
    def jar(self) -> Path:
        if self.api_entry:
            return self.root / self.api_entry
        hits = sorted((self.root / "target").glob("*.jar"))
        if len(hits) != 1:
            raise FileNotFoundError(f"expected one jar under {self.root / 'target'}, found {[h.name for h in hits] or 'none'}")
        return hits[0]

    def run_service(self, port: int, env: dict[str, str] | None = None, timeout_s: float = 180.0) -> ServiceHandle:
        """Start ``java -jar`` on ``http://127.0.0.1:<port>`` and wait until ``GET /health`` is 200.

        Output goes to ``<root>/.exiqo/logs/<artifact>-<port>.log``; ``RuntimeError`` with the log
        tail when the process exits or the health check does not pass in time."""
        jar = self.jar()
        url = f"http://127.0.0.1:{port}"
        logs = self.root / ".exiqo" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"{jar.stem}-{port}.log"
        out = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - handle owned by the child
        proc = subprocess.Popen(
            [self.java, "-jar", str(jar), f"--server.port={port}", "--server.address=127.0.0.1"],
            cwd=self.root, env=self.env(env), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
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


__all__ = ["MavenRunner", "find_tool", "maven_warning_count", "parse_maven_errors"]
