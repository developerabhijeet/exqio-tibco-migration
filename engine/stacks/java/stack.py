"""``JavaStack``: Java 21, Spring Boot 3.5 and Maven behind the ``StackGenerator`` protocol:
``generate_java`` (tests first, ``tests_gen``), ``MavenRunner`` and ``JavaLanguage``."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.stacks.base import Mirrors, ProjectFolder, StackKey, StackLayout
from engine.stacks.java.language import JavaLanguage
from engine.stacks.java.naming import JavaNaming

if TYPE_CHECKING:
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.stacks.java.runner import MavenRunner


class JavaStack:
    key: StackKey = "java"
    label = "Java 21 · Spring Boot 3.5 · Maven"
    required_tools: tuple[str, ...] = ("java", "mvn")
    source_suffixes: tuple[str, ...] = (".java",)
    prompt_pack = "java"
    download_excluded: frozenset[str] = frozenset({"target", ".exiqo"})
    service_process_marker = "java"

    def __init__(self) -> None:
        self._naming = JavaNaming()
        self._language = JavaLanguage()

    @property
    def naming(self) -> JavaNaming:
        return self._naming

    @property
    def language(self) -> JavaLanguage:
        return self._language

    def layout(self, app_name: str) -> StackLayout:
        from engine.stacks.java.generator import CONTRACT_FILE, layout_paths

        paths = layout_paths(app_name)
        projects = {kind: ProjectFolder(name=paths[kind], folder=paths[kind], build_file=None)
                    for kind in ("api", "core", "infrastructure", "tests")}
        return StackLayout(
            solution_file="pom.xml", projects=projects, api_entry=paths["jar"], test_root=paths["tests"],
            dockerfile_context={"artifact": paths["artifact"], "jar": paths["jar"], "package": paths["package"]},
            contract_file=CONTRACT_FILE)

    def generate(self, app: Any, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                 scenarios: list[Any], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
        from engine.stacks.java.generator import generate_java
        return generate_java(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors)

    def runner(self, root: Path, *, env: dict[str, str] | None = None,
               coverage_scope: set[str] | None = None, timeout_s: int | None = None,
               api_entry: str | None = None) -> MavenRunner:
        """A ``MavenRunner``; ``api_entry`` is the jar (``layout(app).api_entry``), the single one
        under ``target`` when omitted."""
        from engine.dotnet_gen.runner import DEFAULT_TIMEOUT_S
        from engine.stacks.java.runner import MavenRunner
        return MavenRunner(root, env=env, coverage_scope=coverage_scope, timeout_s=timeout_s or DEFAULT_TIMEOUT_S,
                           api_entry=api_entry)
