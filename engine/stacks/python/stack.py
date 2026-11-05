"""``PythonStack``: Python 3.12, FastAPI, pytest and uv behind the ``StackGenerator`` protocol:
``generate_python`` (tests first, ``tests_gen``), ``UvRunner`` and ``PythonLanguage``."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.stacks.base import Mirrors, ProjectFolder, StackKey, StackLayout
from engine.stacks.python.language import PythonLanguage
from engine.stacks.python.naming import PythonNaming

if TYPE_CHECKING:
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.stacks.python.runner import UvRunner


class PythonStack:
    key: StackKey = "python"
    label = "Python 3.12 · FastAPI · pytest"
    required_tools: tuple[str, ...] = ("python3", "uv")
    source_suffixes: tuple[str, ...] = (".py",)
    prompt_pack = "python"
    download_excluded: frozenset[str] = frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
                                                   ".exiqo-bin", ".exiqo"})
    service_process_marker = "uvicorn"

    def __init__(self) -> None:
        self._naming = PythonNaming()
        self._language = PythonLanguage()

    @property
    def naming(self) -> PythonNaming:
        return self._naming

    @property
    def language(self) -> PythonLanguage:
        return self._language

    def layout(self, app_name: str) -> StackLayout:
        from engine.stacks.python import versions
        from engine.stacks.python.generator import CONTRACT_FILE, layout_paths

        paths = layout_paths(app_name)
        projects = {kind: ProjectFolder(name=paths[kind], folder=paths[kind], build_file=None)
                    for kind in ("api", "core", "infrastructure", "tests")}
        return StackLayout(
            solution_file="pyproject.toml", projects=projects, api_entry=paths["app"], test_root=paths["tests"],
            dockerfile_context={"package": paths["package"], "app": paths["app"], "python": versions.PYTHON,
                                "base_image": versions.BASE_IMAGE,
                                "build_args": ["BASE_IMAGE_BUILD", "BASE_IMAGE_RUNTIME", "PIP_INDEX_URL"]},
            contract_file=CONTRACT_FILE)

    def fixture_path(self, process: Any, scenario: Any) -> str:
        from engine.stacks.python.tests_gen import scenario_fixture_path
        return scenario_fixture_path(self._naming.type_name(process.name, "Process"), process, scenario)

    def generate(self, app: Any, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                 scenarios: list[Any], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
        from engine.stacks.python.generator import generate_python
        return generate_python(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors)

    def runner(self, root: Path, *, env: dict[str, str] | None = None,
               coverage_scope: set[str] | None = None, timeout_s: int | None = None,
               api_entry: str | None = None) -> UvRunner:
        """A ``UvRunner``; ``api_entry`` is the app factory (``layout(app).api_entry``), the single
        package under ``src`` when omitted."""
        from engine.dotnet_gen.runner import DEFAULT_TIMEOUT_S
        from engine.stacks.python.runner import UvRunner
        return UvRunner(root, env=env, coverage_scope=coverage_scope, timeout_s=timeout_s or DEFAULT_TIMEOUT_S,
                        api_entry=api_entry)
