"""The Python generator (target stacks, Task 14): FastAPI on Python 3.12 from the ServicePlan.

Golden files under ``engine/tests/golden/python/`` hold the repository of ``DealerFundingRequest``
and the file list of ``gmf.credit``. Regenerate them after an intended change with
``UPDATE_GOLDEN=1 pytest engine/tests/test_python_generator.py`` and review the diff.
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

from engine.dotnet_gen.design import build_design_skeleton
from engine.dotnet_gen.patterns import PATTERNS
from engine.dotnet_gen.xpath import FUNCTIONS
from engine.stacks import expr as X
from engine.stacks import get_stack, stack_status
from engine.stacks.base import Mirrors
from engine.stacks.contracts import ContractEnum, ContractModel, ContractProp, ContractRecord
from engine.stacks.python import PythonStack
from engine.stacks.python import contracts as py_contracts
from engine.stacks.python.generator import CONTRACT_FILE, generate_python
from engine.stacks.python.naming import PythonNaming, package_name, snake, upper_snake
from engine.stacks.python.printer import FUNCTIONS as PY_FUNCTIONS
from engine.stacks.python.printer import ExprPrinter
from engine.stacks.python.text import py_string, sql_literal
from engine.stacks.sql import convert_placeholders
from engine.stacks.testindex import INDEX_PATH
from engine.tests.conftest import DFR, FSL, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
TEMPLATES = Path(__file__).resolve().parents[1] / "stacks" / "python" / "templates"
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = "src/gmf_credit"


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
def python_designs(gmf_apps, gmf_app, gmf_processes, gmf_rules, gmf_findings):
    from engine.domains import graph as graph_mod

    graph = graph_mod.build_graph(list(gmf_apps.values()))
    return {pid: build_design_skeleton(gmf_processes[pid], gmf_app, gmf_rules[pid], gmf_findings[pid],
                                       graph_mod.io_card(gmf_processes[pid], gmf_app, graph), stack="python")
            for pid in PROCESS_IDS}


@pytest.fixture(scope="module")
def python_inputs(gmf_app, python_designs, gmf_rules, gmf_findings, gmf_scenarios):
    return gmf_app, PROCESS_IDS, python_designs, gmf_rules, gmf_findings, gmf_scenarios


@pytest.fixture(scope="module")
def python_solution(tmp_path_factory, python_inputs):
    return generate_python(*python_inputs, tmp_path_factory.mktemp("python") / "GmfCredit", Mirrors())


@pytest.fixture(scope="module")
def whole_application(tmp_path_factory, gmf_app):
    pids = [p.id for p in gmf_app.processes]
    return generate_python(gmf_app, pids, {}, {}, {}, [], tmp_path_factory.mktemp("pyall") / "all", Mirrors())


# ------------------------------------------------------------------ the brief's tests
def test_python_golden(python_solution):
    _golden("python/dealer_funding_request_repository.py",
            _file(python_solution, "dealer_funding_request_repository.py").content)
    _golden("python/gmf_credit_files.txt", "\n".join(f.path for f in python_solution.files) + "\n")


def test_python_deterministic(tmp_path, python_inputs, python_solution):
    again = generate_python(*python_inputs, tmp_path / "again", Mirrors())
    assert again.build_hash == python_solution.build_hash
    assert [(f.path, f.content, f.origin) for f in again.files] == \
        [(f.path, f.content, f.origin) for f in python_solution.files]


def _declares(text: str, cls: str, method: str) -> bool:
    return bool(re.search(rf"^class {re.escape(cls)}\b", text, re.MULTILINE)) and \
        bool(re.search(rf"^\s*(?:async )?def {re.escape(method)}\(", text, re.MULTILINE))


def test_every_mapping_row_exists_in_the_tree(python_solution, python_designs):  # spec criterion 3
    files = {f.path: f for f in python_solution.files}
    for d in python_designs.values():
        for r in d.mapping:
            f = files[f"{r.project}/{r.file}"]  # the row's own path, not only a suffix
            assert _declares(f.content, r.class_name, r.method), (r.file, r.class_name, r.method)


def test_untranslatable_step_keeps_tibco_and_placeholder(python_solution):  # Review Focus 1
    agent = [f for f in python_solution.files if f.origin == "agent" and not f.path.startswith("tests/")]
    assert agent and all('NotImplementedError("agent fills:' in f.content and "# TIBCO" in f.content for f in agent)
    cond = _file(python_solution, "dealer_funding_request_conditions.py")
    assert cond.origin == "agent"
    # abs() is not an expression code writes: the TIBCO text stays above the placeholder
    assert re.search(r"# TIBCO condition: abs\(.*\n\s+raise NotImplementedError\(\"agent fills: "
                     r"transition LookupDealToCheckExistingFunding\"\)", cond.content)
    # the conditions code can write are written
    assert 'tib.compare(tib.path(state.get("LookupDealer"), "Record", 1, "status"), "==", "ACTIVE")' in cond.content


def test_sql_verbatim_with_pyformat_placeholders(python_solution, gmf_processes):
    repo = _file(python_solution, "dealer_funding_request_repository.py").content
    fsl = _file(python_solution, "funding_status_lookup_repository.py").content
    assert "where f.deal_id = %s" in fsl and "where deal_id = %s" in repo
    assert "?" not in re.sub(r"#.*", "", fsl) and "@p0" not in repo + fsl
    for pid, text in ((DFR, repo), (FSL, fsl)):
        for act in gmf_processes[pid].activities:
            if act.type.startswith("jdbc."):
                for line in convert_placeholders(str(act.config["sql"]), "pyformat").splitlines():
                    assert line.rstrip() in text, (act.name, line)


def test_runtime_contract_endpoints(python_solution):
    text = "\n".join(f.content for f in python_solution.files if f.path.endswith(".py"))
    for route in ('@router.get("/health")', '@router.post("/auth/dev-token")', '@router.post("/_bus/publish")',
                  '@router.get("/_bus/messages")', '@router.get("/_bus/dead-letters")'):
        assert route in text, route
    config = _file(python_solution, f"{SRC}/config.py").content
    assert '"CONNECTION_STRINGS_GMF_FUNDING_DB": "Host=localhost;' in config  # the development connection
    assert "def canonical(name: str, separator: str = \"__\")" in config


def test_generated_python_compiles(python_solution, whole_application):
    for sol in (python_solution, whole_application):
        for f in sol.files:
            if f.path.endswith(".py"):
                compile(f.content, f.path, "exec")


def test_lock_is_hash_pinned(python_solution):
    lock = _file(python_solution, "requirements.lock").content
    assert lock == (REPO_ROOT / "engine/stacks/python/requirements.lock").read_text()  # copied verbatim
    lines = lock.splitlines()
    reqs = [i for i, ln in enumerate(lines) if ln and not ln.startswith((" ", "#"))]
    assert len(reqs) > 10
    for i in reqs:
        assert re.match(r"^[A-Za-z0-9_.\-\[\]]+==[\w.]+( ;.*)? \\$", lines[i]), lines[i]
        assert lines[i + 1].strip().startswith("--hash=sha256:"), lines[i]
    from engine.stacks.python import versions
    for name, version in versions.DEPENDENCIES + versions.DEV_DEPENDENCIES:
        assert any(ln.startswith(f"{name.split('[')[0]}=={version} ") for ln in lines), name


def test_no_public_index_or_em_dash(python_solution):
    blob = "\n".join(f.content for f in python_solution.files)
    for word in ("pypi.org", "files.pythonhosted.org", "—", "{{", "--index-url"):
        assert word not in blob, word


def test_egress_audit_passes_on_templates_and_output(python_solution):
    """X11: no public host in the shipped templates or the generated project (GMF mirrors only)."""
    import egress_audit

    allow = egress_audit.load_allowlist(REPO_ROOT / "deploy" / "egress-allowlist.txt")
    assert egress_audit.audit_dirs([TEMPLATES, TEMPLATES.parent], allow) == []
    found = [(f.path, v.host) for f in python_solution.files
             for v in egress_audit.scan_text(Path("generated.txt"), f.content) if not allow.allows(v.host)]
    assert found == []


# ------------------------------------------------------------------ naming
def test_python_naming_members():
    n = PythonNaming()
    assert n.type_name("dealer funding-request") == "DealerFundingRequest"
    assert n.method_name("", "LookupDealer") == n.method_name("", "Lookup Dealer") == "lookup_dealer"
    assert n.method_name("Execute", "") == "execute" and n.method_name("", "") == "execute"
    assert n.method_name("Handle", "funding event") == "handle_funding_event"
    assert n.method_name("", "class") == "class_" and n.escape("import") == "import_"
    assert n.method_name("", "9 lives") == "step_9_lives"
    assert n.file_name("lookup dealer") == "lookup_dealer.py"
    assert n.exception_name("DealerInactiveFault") == "DealerInactiveFaultError"
    assert n.config_key("dealerfunding", "DB_URL") == "GMF_DEALERFUNDING_DB_URL"
    assert n.config_key("dealerfunding", "GMF FundingDB", "password") == "GMF_DEALERFUNDING_GMF_FUNDING_DB_PASSWORD"
    assert n.connection_key("GMFFundingDB") == "CONNECTION_STRINGS_GMF_FUNDING_DB"
    assert [n.layer_folder(k, "GmfCredit") for k in ("api", "core", "infrastructure", "tests")] == [
        f"{SRC}/api", f"{SRC}/core", f"{SRC}/infrastructure", "tests"]
    assert n.contract_target("GmfCredit", "DealerFunding", "funding.request") == (
        "gmf_credit.core.contracts.dealerfunding", "contracts/dealerfunding/funding_request.py")
    assert (n.platform, n.config_file, n.messaging_actor) == ("Python", "config.py", "ServiceBus")
    words = {"app": "gmf.credit", "process": "DealerFundingRequest", "activity": "Lookup Dealer", "starter": "get",
             "resource": "EquifaxClient", "fault": "DealerInactiveFault", "target": "Audit Log",
             "destination": "gmf.funding.events"}
    assert n.row_names("jdbc.query", words) == ("dealer_funding_request_repository.py",
                                                "DealerFundingRequestRepository", "lookup_dealer")
    assert n.row_names("http.invoke", words) == ("clients/equifax_client.py", "EquifaxClient", "lookup_dealer")
    assert n.row_names("throw", words) == ("exceptions/dealer_inactive_fault_error.py", "DealerInactiveFaultError",
                                           "__init__")
    assert n.row_names("jms.publish", words) == ("bus.py", "MessageBus", "publish")
    assert n.row_names("input.mapping", words)[2] == "lookup_dealer_mapping"
    assert n.row_names("reply", words) == ("dealer_funding_request_routes.py", "DealerFundingRequestRoutes", "get")
    assert n.row_names("call.process", words) == ("audit_log_service.py", "AuditLogService", "execute")


def test_pattern_paths_cover_every_pattern_under_its_layer():
    n = PythonNaming()
    for name, spec in PATTERNS.items():
        file, _cls, _method = n.pattern_paths(name)
        assert file.startswith(f"{spec.project}/") and file.endswith(".py"), name
    with pytest.raises(KeyError):
        n.pattern_paths("no.such.pattern")


def test_case_rules_match_the_generated_config():
    assert [upper_snake(x) for x in ("GMFFundingDB", "DbUrl", "ConnectionStrings", "FullyQualifiedNamespace")] == [
        "GMF_FUNDING_DB", "DB_URL", "CONNECTION_STRINGS", "FULLY_QUALIFIED_NAMESPACE"]
    assert snake("DealerFundingRequest") == "dealer_funding_request" and package_name("GmfCredit") == "gmf_credit"
    config = (TEMPLATES / "config.py.j2").read_text(encoding="utf-8")
    from engine.stacks.python.naming import WORD
    assert f're.compile(r"{WORD.pattern}")' in config  # one rule on both sides


# ------------------------------------------------------------------ stack
def test_python_stack_is_registered_with_its_contract():
    stack = get_stack("python")
    assert isinstance(stack, PythonStack)
    assert (stack.key, stack.label, stack.required_tools, stack.source_suffixes, stack.prompt_pack,
            stack.download_excluded, stack.service_process_marker) == (
        "python", "Python 3.12 · FastAPI · pytest", ("python3", "uv"), (".py",), "python",
        frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".exiqo-bin", ".exiqo"}), "uvicorn")
    tools = {"python3": {"present": True}, "uv": {"present": True}}
    assert stack_status("python", tools, ["python"]).available is True
    assert stack_status("python", {"python3": {"present": True}}, ["python"]).reason == "toolchain missing: uv"
    layout = stack.layout("GmfCredit")
    assert layout.solution_file == "pyproject.toml" and layout.api_entry == "gmf_credit.main:create_app"
    assert layout.contract_file == CONTRACT_FILE and layout.test_root == "tests"


def test_service_contract_and_test_index(python_solution):
    contract = json.loads(_file(python_solution, CONTRACT_FILE).content)
    assert contract == {"connections": ["GMFFundingDB"], "clients": [],
                        "secrets": [{"name": "ConnectionStrings--GMFFundingDB", "key": "ConnectionStrings__GMFFundingDB"}],
                        "plain_keys": [], "port_env": "PORT", "health": "/health"}
    assert isinstance(json.loads(_file(python_solution, INDEX_PATH).content), list)


def test_layout_and_starters(python_solution):
    paths = {f.path for f in python_solution.files}
    for p in ("pyproject.toml", "requirements.lock", f"{SRC}/__init__.py", f"{SRC}/main.py", f"{SRC}/container.py",
              f"{SRC}/api/funding_status_lookup_routes.py",
              f"{SRC}/infrastructure/messaging/dealer_funding_request_consumer.py",
              f"{SRC}/infrastructure/messaging/__init__.py",
              f"{SRC}/core/contracts/dealerfunding/funding_request.py", f"{SRC}/core/contracts/dealerfunding/__init__.py"):
        assert p in paths, p
    routes = _file(python_solution, "funding_status_lookup_routes.py").content
    assert 'app.add_api_route("/funding/{dealId}", self.get, methods=["GET"], dependencies=dependencies)' in routes
    assert 'parameters["dealId"] = parameter(request.path_params.get("dealId"), "string", "dealId", True)' in routes
    consumer = _file(python_solution, "dealer_funding_request_consumer.py").content
    assert 'DESTINATION = "gmf.dealer.funding.request"' in consumer
    assert 'return properties.get("MsgType") == "FUNDING_REQUEST"' in consumer
    assert 'values = {"Body": {"FundingRequest": body}, ' in consumer
    service = _file(python_solution, "funding_status_lookup_service.py").content
    assert "return ProcessResult.error(FundingStatusLookupMappings.reply_not_found_mapping(state))" in service
    assert "async def execute(self, state: ProcessState) -> ProcessResult:" in service
    dfr = _file(python_solution, "dealer_funding_request_service.py").content
    assert ('await self.bus.publish("gmf.funding.events", tib.path(DealerFundingRequestMappings.'
            'publish_funding_event_mapping(state), "Body", "FundingEvent"))') in dfr
    # a guard clause: the otherwise branch raises, the conditional one carries on
    assert re.search(r"if not DealerFundingRequestConditions\.lookup_dealer_to_lookup_deal\(state\):\n.*\n.*\n\s+raise "
                     r"DealerInactiveFaultError", dfr)
    container = _file(python_solution, "container.py").content
    assert "return DealerFundingRequestService(bus=self.bus, repository=self.dealer_funding_request_repository)" \
        in container
    assert "return [self.funding_status_lookup_routes]" in container


def test_contract_models(python_solution):
    text = _file(python_solution, "dealerfunding/funding_status_lookup_funding.py").content
    assert "class FundingStatusResponseJson(BaseModel):" in text
    assert "model_config = ConfigDict(populate_by_name=True" in text
    assert ('funding_id: int | None = Field(default=None, alias="fundingId", '
            'json_schema_extra={"tibco_path": "/FundingStatusResponse/fundingId"})') in text
    assert "_dt.datetime" in text and "__repr_args__ = masking.masked_repr_args" in text


def test_contract_renderer_types():
    rec = ContractRecord("Loan", props=[
        ContractProp("Count", "count", "int", True, False, "count", tibco_path="/Loan/count"),
        ContractProp("Score", "score", "int", False, False, "score"),
        ContractProp("Notes", "notes", "string", False, True, "notes"),
        ContractProp("Status", "status", "Status", True, False, "status"),
        ContractProp("Class", "class", "string", True, False, "class"),
        ContractProp("Json", "json", "decimal", True, False, "json"),
    ], tibco_path="/Loan")
    model = ContractModel(records={"Loan": rec})
    model.enums["Status"] = ContractEnum("Status", ["ON-HOLD", "1st", "ON_HOLD"])
    text = py_contracts.render(model, "x.xsd", "app")
    assert 'count: int = Field(alias="count", json_schema_extra={"tibco_path": "/Loan/count"})' in text
    assert 'score: int | None = Field(default=None, alias="score")' in text
    assert 'notes: list[str] = Field(default_factory=list, alias="notes")' in text
    assert "status: Status = " in text and "class_: str = " in text and "json_: Decimal = " in text
    assert 'ON_HOLD = "ON-HOLD"' in text and 'V_1ST = "1st"' in text and 'ON_HOLD_2 = "ON_HOLD"' in text
    compile(text, "loan.py", "exec")


def test_the_whole_sample_application_generates(whole_application):
    """Every gmf.credit process (queues, groups, fault handlers, timers, sub-process calls): it
    generates, each design row names code that exists, and each agent file keeps TIBCO text."""
    files = {f.path: f for f in whole_application.files}
    for d in whole_application.designs.values():
        assert d.stack == "python"
        for r in d.mapping:
            assert _declares(files[f"{r.project}/{r.file}"].content, r.class_name, r.method), (r.file, r.method)
    for f in whole_application.files:
        if f.origin == "agent" and not f.path.startswith("tests/"):
            assert 'NotImplementedError("agent fills:' in f.content and "# TIBCO" in f.content, f.path
    folders = {str(Path(p).parent) for p in files if p.startswith("src/") and p.endswith(".py")}
    assert all(f"{d}/__init__.py" in files for d in folders)
    service = files[f"{SRC}/core/nightly_delinquency_scan_service.py"].content
    assert 'for item in tib.items(tib.path(state.get("FindDelinquentAccounts"), "Record")):' in service
    job = _file(whole_application, "nightly_delinquency_scan_job.py").content
    assert 'START_KEY = "JOBS_NIGHTLY_DELINQUENCY_SCAN_START"' in job
    config = files[f"{SRC}/config.py"].content
    assert '"JOBS_NIGHTLY_DELINQUENCY_SCAN_START": "02:00",' in config
    assert "Jobs__NightlyDelinquencyScan__Start" in json.loads(files[CONTRACT_FILE].content)["plain_keys"]
    intake = _file(whole_application, "credit_application_intake_routes.py").content
    assert "invalid = tib.invalid(item, " in intake and "raise BadRequest(masking.mask_text(invalid))" in intake
    assert "tib.invalid" not in _file(whole_application, "funding_status_lookup_routes.py").content  # a GET


def test_a_design_for_another_stack_is_refused(tmp_path, gmf_app, gmf_designs, gmf_rules, gmf_findings):
    with pytest.raises(ValueError, match="is for dotnet, not python"):
        generate_python(gmf_app, [DFR], gmf_designs, gmf_rules, gmf_findings, [], tmp_path / "x", Mirrors())


# ------------------------------------------------------------------ expressions and text
def test_every_xpath_function_has_a_python_helper():
    assert set(PY_FUNCTIONS) == set(FUNCTIONS)
    helpers = (TEMPLATES / "tib_text.py.j2").read_text(encoding="utf-8")
    for py in PY_FUNCTIONS.values():
        assert f"def {py.split('.')[1]}(" in helpers, py


def test_expression_printer():
    p = ExprPrinter({"LookupDeal": "LookupDeal", "get.parameters": "get.parameters"}, {"LookupDeal": ("Record",)})
    e = X.parse_simple("count($LookupDeal/Record) > 0 and not($get.parameters/tns:dealId = '')")
    assert p.cond(e) == ('(tib.compare(tib.count(tib.path(state.get("LookupDeal"), "Record")), ">", 0) and '
                         'not (tib.compare(tib.path(state.get("get.parameters"), "dealId"), "==", "")))')
    assert p.value(X.Lit(Decimal("0.02"))) == 'Decimal("0.02")'
    assert p.value(X.parse_simple("upper-case($get.parameters/x)")) == \
        'tib_text.upper_case(tib.path(state.get("get.parameters"), "x"))'
    assert p.value(X.parse_simple("$_processContext/name")) is None
    assert p.value(X.parse_simple("$LookupDeal/status")) is None


def test_python_text_helpers():
    assert py_string('a"b\\c\n') == '"a\\"b\\\\c\\n"'
    assert sql_literal("select 1") == '"select 1"'
    text = sql_literal('select "x"\nfrom t where a = \'\\d\'"')
    assert eval(text) == 'select "x"\nfrom t where a = \'\\d\'"'


def test_repository_methods_are_unique_with_a_warning(tmp_path, gmf_app, caplog):
    app = gmf_app.model_copy(deep=True)
    deal = next(a for p in app.processes if p.id == DFR for a in p.activities if a.name == "LookupDeal")
    deal.name = "Lookup Dealer"  # gives lookup_dealer, as LookupDealer does
    with caplog.at_level("WARNING"):
        sol = generate_python(app, [DFR], {}, {}, {}, [], tmp_path / "d", Mirrors())
    repo = _file(sol, "dealer_funding_request_repository.py").content
    assert "async def lookup_dealer(self, values" in repo and "async def lookup_dealer2(self, values" in repo
    service = _file(sol, "dealer_funding_request_service.py").content
    assert ".lookup_dealer(" in service and ".lookup_dealer2(" in service
    assert any("repository method lookup_dealer is taken" in r.message for r in caplog.records)
