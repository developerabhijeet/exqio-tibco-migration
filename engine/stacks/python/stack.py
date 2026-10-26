"""``PythonStack``: Python 3.12, FastAPI, pytest and uv behind the ``StackGenerator`` protocol:
``generate_python`` (tests first), ``UvRunner`` and ``PythonLanguage``."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.stacks.base import Mirrors, ProjectFolder, StackKey, StackLayout
from engine.stacks.python.naming import PythonNaming

if TYPE_CHECKING:
    from engine.dotnet_gen.generator import GeneratedSolution
    from engine.stacks.python.language import PythonLanguage
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

    @property
    def naming(self) -> PythonNaming:
        return self._naming

    @property
    def language(self) -> PythonLanguage:
        raise NotImplementedError("PythonLanguage arrives with Task 15")

    def layout(self, app_name: str) -> StackLayout:
        from engine.stacks.python import versions
        from engine.stacks.python.generator import CONTRACT_FILE, layout_paths

        paths = layout_paths(app_name)
        projects = {kind: ProjectFolder(name=paths[kind], folder=paths[kind], build_file=None)
                    for kind in ("api", "core", "infrastructure", "tests")}
        return StackLayout(
            solution_file="pyproject.toml", projects=projects, api_entry=paths["app"], test_root=paths["tests"],
            dockerfile_context={"package": paths["package"], "app": paths["app"], "python": versions.PYTHON,
                                "base_image": versions.BASE_IMAGE, "build_args": ["BASE_IMAGE", "PIP_INDEX_URL"]},
            contract_file=CONTRACT_FILE)

    def fixture_path(self, process: Any, scenario: Any) -> str:
        raise NotImplementedError("the Python tests arrive with Task 15")

    def generate(self, app: Any, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                 scenarios: list[Any], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
        from engine.stacks.python.generator import generate_python
        return generate_python(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors)

    def runner(self, root: Path, *, env: dict[str, str] | None = None,
               coverage_scope: set[str] | None = None, timeout_s: int | None = None,
               api_entry: str | None = None) -> UvRunner:
        raise NotImplementedError("UvRunner arrives with Task 15")
