"""The stack abstraction: what every target stack (.NET, Java, Go, Python) provides.

``StackGenerator`` is the per-stack object the registry hands out; ``StackRunner`` is the
restore/build/format/test/run surface ``DotnetRunner`` already has; ``SourceLanguage`` holds the
text checks the agents use on generated code; ``StackNaming`` turns design names into the
stack's identifiers and paths. See ``docs/superpowers/specs/2026-10-25-target-stacks-design.md``
sections 1 to 3.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:  # the .NET models are the shared result shapes; imported lazily at runtime
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.dotnet_gen.runner import BuildResult, ServiceHandle, StepResult, TestRun

StackKey = Literal["dotnet", "java", "go", "python"]
STACK_KEYS: tuple[StackKey, ...] = ("dotnet", "java", "go", "python")


class Mirrors(BaseModel):
    """Package mirror URLs a generated project restores from; empty = the tool's own default."""

    nuget: str = ""
    maven: str = ""
    goproxy: str = ""
    pypi: str = ""


class ProjectFolder(BaseModel):
    name: str
    folder: str
    build_file: str | None  # the .csproj; None where the stack has one build file at the root


class StackLayout(BaseModel):
    """The layout contract between a stack's generator and infrastructure / Verify."""

    solution_file: str | None
    projects: dict[str, ProjectFolder]  # keyed by project kind: api, core, infrastructure, tests
    api_entry: str  # what ``run_service`` starts
    test_root: str
    dockerfile_context: dict[str, Any]  # template inputs for the stack's Dockerfile
    contract_file: str | None  # ``.exiqo/service-contract.json``; None for .NET


class StackStatus(BaseModel):
    key: str
    label: str
    available: bool
    reason: str | None
    required_tools: list[str]


@runtime_checkable
class StackRunner(Protocol):
    """Restore, build, format, test and run a generated project (``DotnetRunner`` satisfies it)."""

    def restore(self, env: dict[str, str] | None = None) -> StepResult: ...

    def build(self) -> BuildResult: ...

    def format_write(self, whitespace_only: bool = False) -> StepResult: ...

    def format_check(self, whitespace_only: bool = False) -> StepResult: ...

    def test(self) -> TestRun: ...

    def run_service(self, port: int, env: dict[str, str] | None = None,
                    timeout_s: float = 180.0) -> ServiceHandle: ...

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]: ...


@runtime_checkable
class SourceLanguage(Protocol):
    """The code-text checks the agents run on generated source (spec section 3)."""

    comment_prefix: str

    @property
    def placeholder(self) -> re.Pattern[str]: ...

    def test_placeholder(self, test_id: str) -> str: ...

    def strip_comments(self, text: str) -> str: ...

    def code_only(self, text: str) -> str: ...

    def declarations(self, text: str) -> list[str]: ...

    def tibco_comments(self, text: str) -> list[str]: ...

    def is_test_path(self, path: str) -> bool: ...

    def check_test_body(self, body: str, stem: str, kind: str = "rule",
                        json_kinds: dict[str, str] | None = None) -> str | None: ...


@runtime_checkable
class StackNaming(Protocol):
    """Identifiers and paths of the stack (spec section 2; implemented per stack from Task 3)."""

    def type_name(self, text: str) -> str: ...

    def method_name(self, prefix: str, text: str) -> str: ...

    def pattern_paths(self, pattern: str) -> tuple[str, str, str]: ...

    def layer_folder(self, kind: str, app: str) -> str: ...

    def contract_target(self, app: str, module: str, stem: str) -> tuple[str, str]: ...

    def config_key(self, module: str, name: str) -> str: ...


@runtime_checkable
class StackGenerator(Protocol):
    """One object per stack (spec section 1). ``naming`` arrives with Task 3."""

    key: StackKey
    label: str
    required_tools: tuple[str, ...]
    source_suffixes: tuple[str, ...]
    prompt_pack: str  # folder under engine/agents/prompts/; "" = the top level (.NET)
    download_excluded: frozenset[str]
    service_process_marker: str

    @property
    def language(self) -> SourceLanguage: ...

    def layout(self, app_name: str) -> StackLayout: ...

    def generate(self, app: Any, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                 scenarios: list[Any], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution: ...

    def runner(self, root: Path, *, env: dict[str, str] | None = None,
               coverage_scope: set[str] | None = None, timeout_s: int | None = None) -> StackRunner: ...
