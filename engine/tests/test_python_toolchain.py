"""Python tests first, ``UvRunner`` and ``PythonLanguage`` (target stacks, Task 15).

The tests that need uv and Python skip with the reason when ``scripts/toolchain.py`` does not find
them (like ``test_runner.py::needs_dotnet``); everything else runs without a toolchain. The venv is
made from ``python3.12`` when the machine has it (the stack's pinned line), else ``python3``. The
golden file is ``engine/tests/golden/python/test_dealer_funding_request_rules.py``; regenerate it
after an intended change with ``UPDATE_GOLDEN=1`` and review the diff.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import urllib.request
from pathlib import Path

import pytest

from engine.agents.builder import check_filled_file
from engine.agents.test_engineer import placeholder_methods
from engine.dotnet_gen.design import build_design_skeleton
from engine.stacks import get_stack
from engine.stacks.base import Mirrors, SourceLanguage, StackGenerator
from engine.stacks.generic_checks import NO_ASSERTION, check_generic_test_body
from engine.stacks.python.generator import generate_python
from engine.stacks.python.language import PythonLanguage
from engine.stacks.python.runner import UvRunner, find_tool, parse_python_errors
from engine.stacks.testindex import read_index
from engine.tests.conftest import DFR, FSL, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
PYTHON = shutil.which("python3.12") or find_tool("python3")

needs_uv = pytest.mark.skipif(not (find_tool("uv") and PYTHON), reason="uv/python3 not found (scripts/toolchain.py)")


def _golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"golden file {path} missing; run with UPDATE_GOLDEN=1"
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from the golden file"


def _file(solution, suffix: str):
    hits = [f for f in solution.files if f.path.endswith(suffix)]
    assert len(hits) == 1, (suffix, [f.path for f in hits])
    return hits[0]


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def python_solution_with_tests(tmp_path_factory, gmf_apps, gmf_app, gmf_processes, gmf_rules, gmf_findings,
                               gmf_scenarios):
    from engine.domains import graph as graph_mod

    graph = graph_mod.build_graph(list(gmf_apps.values()))
    designs = {pid: build_design_skeleton(gmf_processes[pid], gmf_app, gmf_rules[pid], gmf_findings[pid],
                                          graph_mod.io_card(gmf_processes[pid], gmf_app, graph), stack="python")
               for pid in PROCESS_IDS}
    return generate_python(gmf_app, PROCESS_IDS, designs, gmf_rules, gmf_findings, gmf_scenarios,
                           tmp_path_factory.mktemp("pytests") / "GmfCredit", Mirrors())


@pytest.fixture
def test_database_url():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture(scope="module")
def restored(python_solution_with_tests):
    """The generated project with its venv restored (once for the module)."""
    if not (find_tool("uv") and PYTHON):
        pytest.skip("uv/python3 not found (scripts/toolchain.py)")
    if not os.environ.get("UV_INDEX_URL"):
        pytest.skip("UV_INDEX_URL is not set: the restore refuses without GMF's PyPI mirror (X26)")
    r = UvRunner(python_solution_with_tests.root, python=PYTHON)
    step = r.restore()
    assert step.ok, step.log[-3000:]
    return python_solution_with_tests


# ------------------------------------------------------------------ the brief's tests
def test_python_rules_tests_golden(python_solution_with_tests):
    _golden("python/test_dealer_funding_request_rules.py",
            _file(python_solution_with_tests, "tests/test_dealer_funding_request_rules.py").content)


def test_index_names_every_generated_test(python_solution_with_tests):
    idx = read_index(python_solution_with_tests.root)
    assert {e.kind for e in idx} >= {"rule", "branch", "scenario"}
    for e in idx:
        module, name = e.name.rsplit(".", 1)
        assert module.startswith("tests.test_") and name.startswith("test_") and e.path == module.replace(".", "/") + ".py"
    rule = next(e for e in idx if e.rule_id == "R1")
    assert rule.name == "tests.test_dealer_funding_request_rules.test_r1_a_dealer_must_be_active_to_receive_funding"


def test_parse_compileall_and_ruff_errors(tmp_path):
    log = ("*** Error compiling 'src/app/core/x.py'...\n"
           '  File "src/app/core/x.py", line 3\n    def f(:\n          ^\nSyntaxError: invalid syntax\n'
           f"{tmp_path}/src/app/core/y.py:7:9: F821 Undefined name `fooo`\n"
           "src/app/core/x.py:3:11: SyntaxError: Expected a parameter name\n"
           "Found 2 errors.\n")
    n, errs = parse_python_errors(log, tmp_path)
    assert n == 2
    assert errs == ["src/app/core/x.py:3:11: SyntaxError: Expected a parameter name",
                    "src/app/core/y.py:7:9: F821 Undefined name `fooo`"]
    n, errs = parse_python_errors('  File "src/a.py", line 9\n    x = (\n        ^\nSyntaxError: \'(\' was never closed\n')
    assert errs == ["src/a.py:9: SyntaxError: '(' was never closed"]


def test_python_language_strips_comments_not_strings():
    lang = PythonLanguage()
    assert lang.strip_comments('s = "# no"  # yes') == 's = "# no"  '
    assert lang.strip_comments("x = '''\n# kept\n'''  # gone\n") == "x = '''\n# kept\n'''  \n"
    assert lang.code_only('s = "# no"  # yes') == 's = "    "  '
    # a fragment tokenize refuses still goes through the same rules
    assert lang.strip_comments('def f(:\n    s = "a # b"  # c') == 'def f(:\n    s = "a # b"  '


def test_generic_check_refuses_assert_true():  # Review Focus 3
    lang = PythonLanguage()
    assert lang.check_test_body("assert True", "x") == NO_ASSERTION
    assert lang.check_test_body("assert 1 == 1", "x") == NO_ASSERTION
    assert check_generic_test_body("result = support.call(service.execute, state)\nassert result.status_code == 200",
                                   "python") is None


@needs_uv
def test_generated_python_restores_builds_formats_and_tests(restored, test_database_url):
    r = UvRunner(restored.root, python=PYTHON, env={"TEST_DATABASE_URL": test_database_url})
    build = r.build()
    assert build.ok, build.errors
    assert r.format_write().ok  # the format gate is write-then-check
    check = r.format_check()
    assert check.ok, check.log
    run = r.test()
    assert run.cases and {c.rule_id for c in run.cases if c.rule_id} >= {"R1"} and run.coverage_line is not None
    passed = {c.scenario_id for c in run.cases if c.outcome == "passed"}
    assert {"FundingStatusLookup:happy", "FundingStatusLookup:branch:QueryFundingStatusToReplyNotFound"} <= passed
    failed = {c.name for c in run.cases if c.outcome == "failed"}
    assert all(c.rule_id or c.branch_id or c.scenario_id for c in run.cases)  # every case joined to the index
    assert any("test_r1_" in n for n in failed)  # rule placeholders fail until the Test Engineer writes them


@needs_uv
def test_python_service_answers_health_and_dev_token(restored):
    r = UvRunner(restored.root, python=PYTHON)
    handle = r.run_service(18437, env={"ASPNETCORE_ENVIRONMENT": "Development", "Auth__Mode": "Development",
                                       "Messaging__Mode": "InMemory"})
    try:
        with urllib.request.urlopen(handle.url + "/health", timeout=5) as resp:
            assert resp.status == 200
        req = urllib.request.Request(handle.url + "/auth/dev-token", data=b'{"subject":"t"}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read())["token_type"] == "Bearer"
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(handle.url + "/funding/DL-1", timeout=5)
        assert refused.value.code == 401  # every starter route needs the token
    finally:
        handle.stop()


# ------------------------------------------------------------------ generated tests and index
def test_rules_tests_have_one_placeholder_per_approved_rule(python_solution_with_tests, gmf_rules):
    text = _file(python_solution_with_tests, "tests/test_dealer_funding_request_rules.py").content
    approved = [r.id for r in gmf_rules[DFR] if r.status in ("approved", "edited")]
    assert approved == ["R1", "R2", "R3"]
    for rid in approved:
        assert f'pytest.fail("no test body for {rid}")' in text
    assert "R4" not in text  # a draft rule gets no test
    assert "def test_r1_a_dealer_must_be_active_to_receive_funding(support: ScenarioSupport) -> None:" in text


def test_index_entries_match_the_test_engineer_lookup(python_solution_with_tests):
    """The Test Engineer finds a test by path, process id, kind and the last segment of its name."""
    files = {f.path: f.content for f in python_solution_with_tests.files}
    idx = read_index(python_solution_with_tests.root)
    for e in idx:
        assert e.path in files and e.process_id in (DFR, FSL)
        assert f"def {e.name.rsplit('.', 1)[1]}(support: ScenarioSupport) -> None:" in files[e.path]
    rules = [e for e in idx if e.kind == "rule" and e.process_id == DFR]
    assert [e.rule_id for e in rules] == ["R1", "R2", "R3"] and not any(e.has_body for e in rules)
    found = placeholder_methods(files[rules[0].path], "python", rules)
    assert [m.ref for m in found] == ["R1", "R2", "R3"]
    branches = [e for e in idx if e.kind == "branch" and e.process_id == FSL]
    assert branches and all(e.has_body for e in branches)  # every FSL branch has a driving scenario
    assert {"FundingStatusLookup:happy", "DealerFundingRequest:happy"} <= {e.scenario_id for e in idx}


def test_scenario_drivers_follow_the_starter(python_solution_with_tests):
    fsl = _file(python_solution_with_tests, "tests/test_funding_status_lookup_scenario.py").content
    assert 'support.run_request("FundingStatusLookup", scenario)' in fsl
    dfr = _file(python_solution_with_tests, "tests/test_dealer_funding_request_scenario.py").content
    assert ('support.run_message("DealerFundingRequest", scenario, DealerFundingRequestConsumer.ENTITY, '
            '{"MsgType": "FUNDING_REQUEST"}, consumer.handle)') in dfr
    branch = _file(python_solution_with_tests, "tests/test_dealer_funding_request_branch.py").content
    assert 'pytest.fail("no test body for branch LookupDealerToThrowDealerInactive: no scenario takes this link")' \
        in branch
    assert '_run(support, "happy")' in branch


def test_fixtures_are_the_dotnet_documents(python_solution_with_tests, gmf_processes, gmf_scenarios):
    files = {f.path: f.content for f in python_solution_with_tests.files}
    happy = json.loads(files["tests/fixtures/FundingStatusLookup/happy.json"])
    assert happy["request"]["path"] == "/funding/DL-50001" and happy["expected"]["output"]["status"] == 200
    assert "body.fundedAt" in happy["expected"]["output_noise"]
    dfr = json.loads(files["tests/fixtures/DealerFundingRequest/happy.json"])
    row = next(e for e in dfr["expected"]["side_effects"] if e["kind"] == "row_written")
    assert row["table"] == "gmf.funding" and "funded_at" in row["noise"]
    assert "tests/database/schema.sql" in files and "tests/database/seed.sql" in files
    scenario = next(s for s in gmf_scenarios if s.id == "FundingStatusLookup:happy")
    assert get_stack("python").fixture_path(gmf_processes[FSL], scenario) == "tests/fixtures/FundingStatusLookup/happy.json"


def test_test_support_reads_the_test_database_and_skips_without_it(python_solution_with_tests):
    conftest = _file(python_solution_with_tests, "tests/conftest.py").content
    assert 'CONNECTION_KEYS: tuple[str, ...] = ("CONNECTION_STRINGS_GMF_FUNDING_DB", )' in conftest
    assert "with TestClient(create_app(service_settings())) as running:" in conftest
    support = _file(python_solution_with_tests, "tests/scenario_support.py").content
    assert 'pytest.skip("TEST_DATABASE_URL is not set")' in support
    assert 'SCHEMAS: tuple[str, ...] = ("gmf", )' in support and 'name.endswith("_test")' in support


# ------------------------------------------------------------------ PythonLanguage
def test_python_language_is_the_stack_language():
    stack = get_stack("python")
    assert isinstance(stack, StackGenerator)
    lang = stack.language
    assert isinstance(lang, PythonLanguage) and isinstance(lang, SourceLanguage)
    assert lang.test_placeholder("R1") == 'pytest.fail("no test body for R1")'
    assert lang.placeholder.search('raise NotImplementedError("agent fills: X")')
    assert lang.is_test_path("tests/test_a_rules.py") and not lang.is_test_path("src/app/core/a.py")
    assert lang.tibco_comments('    # TIBCO: x = 1\ns = "# TIBCO not a comment"\nx = 1  # TIBCO trailing\n') == \
        ["# TIBCO: x = 1"]


def test_declarations_are_def_and_class_headers_however_wrapped():
    lang = PythonLanguage()
    flat = '''class LoanService:
    """doc"""

    def __init__(self, *, bus: MessageBus, repository: LoanRepository) -> None:
        self.bus = bus

    async def execute(self, state: ProcessState) -> ProcessResult:
        if state.get("x"):
            return await self.lookup_dealer(state)
        return state.finish()

    @staticmethod
    def lookup_dealer_mapping(state: ProcessState) -> dict[str, Any]:
        raise NotImplementedError("agent fills: x")
'''
    wrapped = flat.replace("def __init__(self, *, bus: MessageBus, repository: LoanRepository) -> None:",
                           "def __init__(\n        self,\n        *,\n        bus: MessageBus,\n"
                           "        repository: LoanRepository,\n    ) -> None:")
    want = ["class LoanService", "def __init__(self, *, bus: MessageBus, repository: LoanRepository) -> None",
            "async def execute(self, state: ProcessState) -> ProcessResult",
            "def lookup_dealer_mapping(state: ProcessState) -> dict[str, Any]"]
    assert lang.declarations(flat) == want and lang.declarations(wrapped) == want
    assert lang.declarations("def test_r1_x(support: ScenarioSupport) -> None:") == [
        "def test_r1_x(support: ScenarioSupport) -> None"]


MAIN = "src/gmf_credit/core/loan_mappings.py"
SCAFFOLD = '''class LoanMappings:
    @staticmethod
    def total_mapping(state: ProcessState) -> dict[str, Any]:
        # TIBCO: total = $Start/a + $Start/b
        raise NotImplementedError("agent fills: total mapping")
'''


def _fill(body: str) -> str:
    return SCAFFOLD.replace('        raise NotImplementedError("agent fills: total mapping")\n', body)


@pytest.mark.parametrize(("body", "reason"), [
    ("        assert state is not None\n        return {}\n", "assertion"),
    ("        pytest.fail('x')\n", "assertion"),
    ("        return {}\n\n    def test_extra(self):\n        pass\n", "test function"),
    ("        return {}  # noqa: F821\n", "suppression"),
    ("        try:\n            return {}\n        except Exception:\n            return {}\n", "swallow a TIBCO fault"),
    ("        try:\n            return {}\n        except:\n            return {}\n", "swallow a TIBCO fault"),
    ("        try:\n            return {}\n        except DealerInactiveFaultError:\n            return {}\n",
     "swallow a TIBCO fault"),
    ("        return {'p': open('/etc/passwd').read()}\n", "outside the project"),
])
def test_python_fill_may_not_introduce(body, reason):
    assert reason in check_filled_file(MAIN, SCAFFOLD, _fill(body), stack="python")


def test_python_fill_may_catch_a_specific_error_and_mention_assert_in_a_comment():
    body = ("        # no assert here, no pytest either\n        note = 'assert nothing'\n        try:\n"
            "            return {'total': tib.number(tib.path(state.get('Start'), 'a'))}\n"
            "        except ValueError as ex:\n            raise RuntimeError(note) from ex\n")
    assert check_filled_file(MAIN, SCAFFOLD, _fill(body), stack="python") is None


# ------------------------------------------------------------------ UvRunner without uv
def _fake(tmp_path: Path, name: str, code: int = 0) -> Path:
    log = tmp_path / "calls.txt"
    exe = tmp_path / name
    exe.write_text(f'#!/bin/sh\necho "{name} $@" >> {log}\nenv >> {log}.env\nexit {code}\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_runner_commands_are_the_spec_s(tmp_path, monkeypatch):
    root = tmp_path / "app"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    for tool in ("python", "ruff"):
        _fake(root / ".venv" / "bin", tool).rename(root / ".venv" / "bin" / tool)
    uv = _fake(tmp_path, "uv")
    monkeypatch.setenv("UV_INDEX_URL", "https://pypi.gmf.example/simple")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")  # never inherited
    r = UvRunner(root, python="/usr/bin/python3", uv=str(uv))
    assert r.restore().ok and r.build().ok and r.format_write().ok and r.format_check().ok
    r.test()
    calls = (tmp_path / "calls.txt").read_text().splitlines() + \
        (root / ".venv" / "bin" / "calls.txt").read_text().splitlines()
    venv = root / ".venv" / "bin"
    assert calls[:2] == ["uv venv .venv --python /usr/bin/python3 --allow-existing",
                         f"uv pip install --require-hashes --no-build -r requirements.lock --python {venv}/python"]
    assert calls[2] == "python -c import sys; sys.exit(sys.version_info[:2] != (3, 12))"  # the 3.12 line
    assert calls[3:] == ["python -m compileall -q src",
                         "ruff check --no-cache --select E9,F63,F7,F82 --output-format concise src tests",
                         "ruff format --no-cache src tests", "ruff format --no-cache --check src tests",
                         ("python -m pytest --junitxml=.exiqo-bin/junit.xml --cov=src "
                         "--cov-report=xml:.exiqo-bin/coverage.xml -q")]
    env = (tmp_path / "calls.txt.env").read_text()
    assert "UV_INDEX_URL=https://pypi.gmf.example/simple" in env and f"VIRTUAL_ENV={root / '.venv'}" in env
    assert f"PATH={venv}" in env and "OPENAI_API_KEY" not in env and "UV_PYTHON_DOWNLOADS=never" in env


def test_runner_passes_no_index_when_none_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    assert "UV_INDEX_URL" not in UvRunner(tmp_path, python="python3", uv="uv").env()


def test_a_failed_test_run_is_not_ok(tmp_path):
    root = tmp_path / "app"
    (root / ".venv" / "bin").mkdir(parents=True)
    _fake(root / ".venv" / "bin", "python", code=1).rename(root / ".venv" / "bin" / "python")
    run = UvRunner(root, python="python3", uv="uv").test()
    assert not run.ok and run.cases == []


def test_the_runner_finds_the_app_factory(tmp_path):
    (tmp_path / "src" / "gmf_credit").mkdir(parents=True)
    (tmp_path / "src" / "gmf_credit" / "main.py").write_text("")
    assert UvRunner(tmp_path, python="python3", uv="uv").app() == "gmf_credit.main:create_app"
    assert UvRunner(tmp_path, python="python3", uv="uv", api_entry="x.main:create_app").app() == "x.main:create_app"
    layout = get_stack("python").layout("GmfCredit")
    assert get_stack("python").runner(tmp_path, api_entry=layout.api_entry).app() == "gmf_credit.main:create_app"


# ------------------------------------------------------------------ Dockerfile and pipelines
@pytest.fixture(scope="module")
def python_infra(python_solution_with_tests, gmf_app):
    from engine.infra_gen import generator as infra

    designs = list(python_solution_with_tests.designs.values())
    files = infra.generate_infra(gmf_app, designs, python_solution_with_tests.root, python_solution_with_tests.name,
                                 stack="python")
    return {f.path: f.content for f in files}


def test_python_dockerfile_installs_the_lock_and_runs_as_10001(python_infra, python_solution_with_tests):
    import re

    df = python_infra["Dockerfile"]
    # no silent Docker Hub default (as for Java); the upstream image is named for GMF to mirror
    assert re.search(r"^ARG BASE_IMAGE_BUILD$", df, re.MULTILINE) and re.search(r"^ARG BASE_IMAGE_RUNTIME$", df, re.MULTILINE)
    assert "python:3.12-slim" in df and "=python:" not in df
    assert "ARG PIP_INDEX_URL=" in df and "pip install --require-hashes --no-deps --only-binary=:all: -r /tmp/requirements.lock" in df
    assert re.search(r"^USER 10001$", df, re.MULTILINE) and "EXPOSE 8080" in df and "PORT=8080" in df
    assert 'ENTRYPOINT ["python", "-m", "gmf_credit.main"]' in df
    for line in df.splitlines():  # every COPY source exists in the generated project
        if line.startswith("COPY ") and "--from" not in line:
            assert (python_solution_with_tests.root / line.split()[1]).exists(), line
    ignore = python_infra[".dockerignore"]
    assert "**/.venv" in ignore and "**/__pycache__" in ignore and "**/bin" not in ignore
    main = _file(python_solution_with_tests, "src/gmf_credit/main.py").content
    assert 'port=int(os.environ.get("PORT", "8080"))' in main


def test_python_infra_secrets_are_the_contract_s(python_infra, python_solution_with_tests):
    from engine.tests.test_infra import secret_names_in

    contract = json.loads((python_solution_with_tests.root / ".exiqo/service-contract.json").read_text())
    assert set(secret_names_in(python_infra)) == {s["name"] for s in contract["secrets"]}
    assert "run-local.sh" not in python_infra


def test_python_pipelines_build_with_uv(python_infra):
    import yaml

    gh = python_infra[".github/workflows/gmf-credit.yml"]
    ado = python_infra["azure-pipelines.yml"]
    for text in (gh, ado):
        assert "uv pip install --require-hashes --no-build -r requirements.lock" in text
        assert ".venv/bin/ruff check --select E9,F63,F7,F82 src tests" in text and "--junitxml=.exiqo-bin/junit.xml" in text
        assert "--build-arg" in text and "PIP_INDEX_URL" in text and "BASE_IMAGE_RUNTIME" in text
        assert "for name in BASE_IMAGE_BUILD BASE_IMAGE_RUNTIME; do" in text
        assert "mvn " not in text and "setup-java" not in text and "JavaToolInstaller" not in text
        assert "DotNetCoreCLI" not in text and "setup-dotnet" not in text
    assert "actions/setup-python@v5" in gh and "UsePythonVersion@0" in ado and "Publish Cobertura coverage" in ado
    assert yaml.safe_load(gh)["jobs"] and yaml.safe_load(ado)["stages"]
    low = "\n".join(t for p, t in python_infra.items() if not p.startswith("infra/modules/")).lower()
    assert "pypi.org" not in low and "pythonhosted" not in low


def test_the_design_scaffold_names_files_the_generator_writes(python_solution_with_tests):
    paths = {f.path for f in python_solution_with_tests.files}
    for design in python_solution_with_tests.designs.values():
        for project in design.solution.projects:
            for name in project.files:
                assert f"{project.name}/{name}" in paths, (project.kind, name)


# ------------------------------------------------------------------ review fix round 1
def test_restore_refuses_an_interpreter_off_the_3_12_line(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_INDEX_URL", "https://pypi.gmf.example/simple")
    root = tmp_path / "app"
    (root / ".venv" / "bin").mkdir(parents=True)
    _fake(root / ".venv" / "bin", "python", code=1).rename(root / ".venv" / "bin" / "python")
    step = UvRunner(root, python="/usr/bin/python3", uv=str(_fake(tmp_path, "uv"))).restore()
    assert not step.ok and "Python 3.12" in step.log


def test_the_runner_asks_uv_for_the_pinned_line_by_default(tmp_path):
    assert UvRunner(tmp_path, uv="uv").python == "3.12"


def test_runtime_lock_has_no_dev_tools_and_the_image_installs_it(python_solution_with_tests, python_infra):
    files = {f.path: f.content for f in python_solution_with_tests.files}
    runtime, full = files["requirements.runtime.lock"], files["requirements.lock"]
    names = {ln.split("==")[0] for ln in runtime.splitlines() if ln and not ln.startswith((" ", "#"))}
    assert {"fastapi", "uvicorn", "psycopg", "pydantic", "pyjwt"} <= names
    assert not names & {"pytest", "pytest-cov", "coverage", "ruff", "pluggy", "iniconfig"}
    for ln in runtime.splitlines():  # the same files, the same hashes as the full lock
        if ln.strip().startswith("--hash=") or (ln and not ln.startswith("#")):
            assert ln in full, ln
    df = python_infra["Dockerfile"]
    assert "COPY requirements.runtime.lock /tmp/requirements.lock" in df
    assert "--only-binary=:all:" in df and "TZ=UTC" in df


def test_ci_installs_uv_by_hash(python_solution_with_tests, python_infra):
    files = {f.path: f.content for f in python_solution_with_tests.files}
    tool = files["uv-tool.lock"]
    assert tool.startswith("# ") and "uv==0.11.7 " in tool and "--hash=sha256:" in tool
    for path in (".github/workflows/gmf-credit.yml", "azure-pipelines.yml"):
        text = python_infra[path]
        assert "pip install --require-hashes --no-deps --only-binary=uv -r uv-tool.lock" in text, path
        assert "setup-uv" not in text and '"uv==0.11.7"' not in text, path
    assert "uv pip install --require-hashes --no-build" in python_infra["azure-pipelines.yml"]


def test_contract_models_take_scalars_for_strings_as_jackson_does():
    from engine.stacks.contracts import ContractModel, ContractProp, ContractRecord
    from engine.stacks.python import contracts as py_contracts

    rec = ContractRecord("Deal", props=[ContractProp("DealId", "dealId", "string", True, False, "dealId")])
    text = py_contracts.render(ContractModel(records={"Deal": rec}), "x.xsd", "app")
    assert "coerce_numbers_to_str=True" in text and 'deal_id: tib.Text = Field(alias="dealId")' in text


def test_service_auth_and_database_review_fixes(python_solution_with_tests):
    files = {f.path: f.content for f in python_solution_with_tests.files}
    auth = files["src/gmf_credit/api/auth.py"]
    assert "await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)" in auth
    assert "connect_timeout" in files["src/gmf_credit/infrastructure/database.py"]
    assert "add_exception_handler(StarletteHTTPException" in files["src/gmf_credit/main.py"]


GENERATED_CHECK = """
import json, sys
sys.path.insert(0, sys.argv[1])
import warnings
warnings.simplefilter("ignore")
from fastapi.testclient import TestClient
from gmf_credit.config import Settings
from gmf_credit.core import tib
from gmf_credit.core.contracts.dealerfunding.funding_status_lookup_funding import FundingStatusResponseJson as M
from gmf_credit.main import create_app
out = {"number": tib.invalid({"dealId": 123}, M), "bool": tib.invalid({"dealId": True}, M),
       "decimal": tib.invalid({"dealId": 1.5}, M), "wrong": tib.invalid({"fundingId": "x"}, M),
       "text": M.model_validate({"dealId": True, "status": 7}).model_dump(include={"deal_id", "status"})}
with TestClient(create_app(Settings.load({"ASPNETCORE_ENVIRONMENT": "Development"}))) as c:
    r = c.get("/funding/D1")
    out["401"] = [r.status_code, r.headers["content-type"], r.json().get("title"), r.headers.get("www-authenticate")]
print(json.dumps(out, default=str))
"""


@needs_uv
def test_generated_contracts_and_401_in_the_service(restored, tmp_path):
    import subprocess

    script = tmp_path / "check.py"
    script.write_text(GENERATED_CHECK)
    venv_python = restored.root / ".venv" / "bin" / "python"
    proc = subprocess.run([str(venv_python), "-I", str(script), str(restored.root / "src")], capture_output=True,
                          text=True, timeout=120, check=False, env={"PATH": os.environ.get("PATH", "")})
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["number"] is None and out["bool"] is None and out["decimal"] is None  # as Jackson reads them
    assert out["wrong"] and "funding" in out["wrong"].lower()  # a real type error still answers 400
    assert out["text"] == {"deal_id": "true", "status": "7"}
    assert out["401"] == [401, "application/problem+json", "Unauthorized", "Bearer"]


# ------------------------------------------------------------------ X26: no mirror, no restore
def test_restore_refuses_without_a_pypi_mirror(tmp_path, monkeypatch):
    """Never pypi.org: an unset or empty UV_INDEX_URL stops the restore before uv runs."""
    uv = _fake(tmp_path, "uv")
    for value in (None, "", "  "):
        if value is None:
            monkeypatch.delenv("UV_INDEX_URL", raising=False)
        else:
            monkeypatch.setenv("UV_INDEX_URL", value)
        step = UvRunner(tmp_path, python="/usr/bin/python3", uv=str(uv)).restore()
        assert not step.ok and "UV_INDEX_URL" in step.log and "public PyPI index" in step.log
    assert not (tmp_path / "calls.txt").exists()


def test_restore_takes_the_mirror_from_the_runner_env(tmp_path, monkeypatch):
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    root = tmp_path / "app"
    (root / ".venv" / "bin").mkdir(parents=True)
    _fake(root / ".venv" / "bin", "python").rename(root / ".venv" / "bin" / "python")
    r = UvRunner(root, python="/usr/bin/python3", uv=str(_fake(tmp_path, "uv")),
                 env={"UV_INDEX_URL": "https://pypi.gmf.example/simple"})
    assert r.restore().ok


def test_python_bundle_fails_closed_without_the_pypi_mirror(python_infra):
    df = python_infra["Dockerfile"]
    assert 'test -n "${PIP_INDEX_URL}"' in df and "never the public index" in df
    assert "unset PIP_INDEX_URL" not in df
    gh, ado = python_infra[".github/workflows/gmf-credit.yml"], python_infra["azure-pipelines.yml"]
    for text in (gh, ado):
        assert 'test -n "${UV_INDEX_URL:-}"' in text and "never the public index" in text
        assert "unset UV_INDEX_URL" not in text


def test_runner_adds_the_mirror_ca_to_the_system_roots(tmp_path, monkeypatch):
    from engine.stacks import cabundle

    system, pem = tmp_path / "system.pem", tmp_path / "ca.pem"
    system.write_text("-----BEGIN CERTIFICATE-----\nSYSTEM\n-----END CERTIFICATE-----\n")
    pem.write_text("-----BEGIN CERTIFICATE-----\nMIRROR\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(cabundle, "system_bundle", lambda: system)
    monkeypatch.setattr(cabundle, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("MIRROR_CA_BUNDLE", str(pem))
    text = Path(UvRunner(tmp_path, python="python3", uv="uv").env()["SSL_CERT_FILE"]).read_text()
    assert "SYSTEM" in text and "MIRROR" in text
