"""``DotnetStack``: today's .NET generator, runner and checks behind the ``StackGenerator`` protocol.

Wrapped, not refactored: ``generate`` calls ``generate_solution`` unchanged, so the .NET output
and its golden files stay byte-identical.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.stacks.base import Mirrors, ProjectFolder, StackKey, StackLayout
from engine.stacks.dotnet.language import DotnetLanguage

if TYPE_CHECKING:
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.dotnet_gen.runner import DotnetRunner


class DotnetStack:
    key: StackKey = "dotnet"
    label = "C# · .NET 10 · ASP.NET Core"
    required_tools: tuple[str, ...] = ("dotnet",)
    source_suffixes: tuple[str, ...] = (".cs",)
    prompt_pack = ""  # the existing top-level prompts are the .NET pack
    download_excluded: frozenset[str] = frozenset({"bin", "obj", "TestResults", ".exiqo"})
    service_process_marker = "dotnet"

    def __init__(self) -> None:
        self._language = DotnetLanguage()

    @property
    def language(self) -> DotnetLanguage:
        return self._language

    def layout(self, app_name: str) -> StackLayout:
        from engine.dotnet_gen.generator import solution_layout

        raw = solution_layout(app_name)
        projects = {kind: ProjectFolder(name=p["name"], folder=p["folder"], build_file=p["csproj"])
                    for kind, p in raw["projects"].items()}
        return StackLayout(
            solution_file=raw["solution"], projects=projects, api_entry=raw["api_project"],
            test_root=raw["projects"]["tests"]["folder"],
            dockerfile_context={k: raw[k] for k in ("api_project", "api_assembly", "api_dll", "test_project")},
            contract_file=None)

    def generate(self, app: Any, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                 scenarios: list[Any], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
        from engine.dotnet_gen.generator import generate_solution
        return generate_solution(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors.nuget)

    def runner(self, root: Path, *, env: dict[str, str] | None = None,
               coverage_scope: set[str] | None = None, timeout_s: int | None = None) -> DotnetRunner:
        from engine.dotnet_gen.runner import DEFAULT_TIMEOUT_S, DotnetRunner
        return DotnetRunner(root, timeout_s=timeout_s or DEFAULT_TIMEOUT_S, env=env, coverage_scope=coverage_scope)
