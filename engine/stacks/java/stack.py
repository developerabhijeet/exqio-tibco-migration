"""``JavaStack``: Java 21, Spring Boot 3.5 and Maven behind the ``StackGenerator`` protocol.

The runner (``MavenRunner``) and the agents' source checks (``JavaLanguage``) arrive with Task 8;
until then ``runner`` and ``language`` raise ``NotImplementedError`` naming what is missing, so a
build that reaches them fails with a clear job error instead of a wrong one.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.stacks.base import Mirrors, ProjectFolder, StackKey, StackLayout
from engine.stacks.java.naming import JavaNaming

if TYPE_CHECKING:
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.stacks.base import SourceLanguage, StackRunner

_PENDING = "the Java {what} is not implemented yet (target stacks Task 8)"


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

    @property
    def naming(self) -> JavaNaming:
        return self._naming

    @property
    def language(self) -> SourceLanguage:
        raise NotImplementedError(_PENDING.format(what="source language checks (JavaLanguage)"))

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
               api_entry: str | None = None) -> StackRunner:
        raise NotImplementedError(_PENDING.format(what="runner (MavenRunner)"))
