"""``DotnetStackRunner``: ``DotnetRunner`` with the ``StackRunner`` call signatures.

``DotnetRunner`` takes a project first (``restore(project, env)``, ``run_service(project, port,
...)``); the stack protocol has no project argument, because every other stack has one build
file and one service. This subclass knows the API project and fills that argument in, so
``get_stack(key).runner(root).run_service(port, env, timeout_s)`` works on .NET too.
``DotnetRunner`` itself is unchanged.
"""
from __future__ import annotations

from pathlib import Path

from engine.dotnet_gen.runner import (
    DEFAULT_TIMEOUT_S,
    BuildResult,
    DotnetRunner,
    ServiceHandle,
    StepResult,
    TestRun,
)

_BUILD_OUTPUT = frozenset({"bin", "obj"})


class DotnetStackRunner(DotnetRunner):
    def __init__(self, root: Path, *, dotnet: str | None = None, env: dict[str, str] | None = None,
                 coverage_scope: set[str] | None = None, timeout_s: int | None = None,
                 api_entry: str | None = None) -> None:
        """``dotnet`` is the CLI to run (found on the machine when omitted, as the other stacks'
        ``java=``/``go=``/``uv=``); ``api_entry`` is the API project (``src/<App>.Api/<App>.Api.csproj``, relative to
        ``root``); when omitted, the single ``*.Api.csproj`` under ``root`` is used."""
        super().__init__(root, dotnet=dotnet, timeout_s=timeout_s or DEFAULT_TIMEOUT_S, env=env, coverage_scope=coverage_scope)
        self.api_entry = api_entry

    def api_project(self) -> str:
        """The API project relative to ``root`` (discovered when it was not given)."""
        if self.api_entry:
            return self.api_entry
        hits = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*.Api.csproj")
                      if not (_BUILD_OUTPUT & set(p.relative_to(self.root).parts)))
        if len(hits) != 1:
            raise FileNotFoundError(f"expected one *.Api.csproj under {self.root}, found {hits or 'none'}")
        return hits[0]

    # The protocol signatures. The base methods take ``project``/``env`` as well; the stack calls
    # them for the whole solution (or the API project for ``run_service``).
    def restore(self, env: dict[str, str] | None = None) -> StepResult:  # type: ignore[override]
        return super().restore(None, env=env)

    def build(self) -> BuildResult:  # type: ignore[override]
        return super().build()

    def format_write(self, whitespace_only: bool = False) -> StepResult:  # type: ignore[override]
        return super().format_write(whitespace_only)

    def format_check(self, whitespace_only: bool = False) -> StepResult:  # type: ignore[override]
        return super().format_check(whitespace_only)

    def test(self) -> TestRun:  # type: ignore[override]
        return super().test()

    def run_service(self, port: int, env: dict[str, str] | None = None,  # type: ignore[override]
                    timeout_s: float = 180.0) -> ServiceHandle:
        return super().run_service(self.api_project(), port, env=env, timeout_s=timeout_s)
