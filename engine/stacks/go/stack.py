"""``GoStack``: Go, ``net/http`` and go modules behind the ``StackGenerator`` protocol:
``generate_go`` (tests first, ``tests_gen``), ``GoRunner`` and ``GoLanguage``."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.stacks.base import Mirrors, ProjectFolder, StackKey, StackLayout
from engine.stacks.go.language import GoLanguage
from engine.stacks.go.naming import GoNaming

if TYPE_CHECKING:
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.stacks.go.runner import GoRunner


class GoStack:
    key: StackKey = "go"
    label = "Go · net/http · go modules"
    required_tools: tuple[str, ...] = ("go",)
    source_suffixes: tuple[str, ...] = (".go",)
    prompt_pack = "go"
    download_excluded: frozenset[str] = frozenset({".exiqo-bin", ".exiqo"})
    service_process_marker = ".exiqo-bin/server"

    def __init__(self) -> None:
        self._naming = GoNaming()
        self._language = GoLanguage()

    @property
    def naming(self) -> GoNaming:
        return self._naming

    @property
    def language(self) -> GoLanguage:
        return self._language

    def layout(self, app_name: str) -> StackLayout:
        from engine.stacks.go import versions
        from engine.stacks.go.generator import CONTRACT_FILE, layout_paths

        paths = layout_paths(app_name)
        projects = {kind: ProjectFolder(name=paths[kind], folder=paths[kind], build_file=None)
                    for kind in ("api", "core", "infrastructure", "tests")}
        return StackLayout(
            solution_file="go.mod", projects=projects, api_entry=paths["binary"], test_root=paths["tests"],
            dockerfile_context={"module": paths["module"], "binary": paths["binary"], "main": f"./{paths['main']}",
                                "go_version": versions.GO_VERSION,
                                "base_image_build": versions.BASE_IMAGE_BUILD,
                                "base_image_runtime": versions.BASE_IMAGE_RUNTIME,
                                "build_args": ["BASE_IMAGE_BUILD", "BASE_IMAGE_RUNTIME", "GOPROXY"]},
            contract_file=CONTRACT_FILE)

    def fixture_path(self, process: Any, scenario: Any) -> str:
        from engine.stacks.go.tests_gen import scenario_fixture_path
        return scenario_fixture_path(self._naming.type_name(process.name, "Process"), process, scenario)

    def generate(self, app: Any, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                 scenarios: list[Any], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
        from engine.stacks.go.generator import generate_go
        return generate_go(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors)

    def runner(self, root: Path, *, env: dict[str, str] | None = None,
               coverage_scope: set[str] | None = None, timeout_s: int | None = None,
               api_entry: str | None = None) -> GoRunner:
        """A ``GoRunner``; ``api_entry`` is the binary ``build`` writes (``.exiqo-bin/server``)."""
        from engine.dotnet_gen.runner import DEFAULT_TIMEOUT_S
        from engine.stacks.go.runner import GoRunner
        return GoRunner(root, env=env, coverage_scope=coverage_scope, timeout_s=timeout_s or DEFAULT_TIMEOUT_S,
                        api_entry=api_entry)
