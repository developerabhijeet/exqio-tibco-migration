"""The Go generator (target stacks, Task 12): net/http and go modules from the ServicePlan.

Golden files under ``engine/tests/golden/go/`` hold the repository of ``DealerFundingRequest`` and the
file list of ``gmf.credit``. Regenerate them after an intended change with
``UPDATE_GOLDEN=1 pytest engine/tests/test_go_generator.py`` and review the diff.
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
from engine.stacks.base import Mirrors, StackGenerator
from engine.stacks.contracts import ContractEnum, ContractModel, ContractProp, ContractRecord
from engine.stacks.go import GoStack, versions
from engine.stacks.go import contracts as go_contracts
from engine.stacks.go.generator import CONTRACT_FILE, generate_go, prune_imports
from engine.stacks.go.language import GoLanguage
from engine.stacks.go.naming import GoNaming, env_key, go_file, module_path, snake
from engine.stacks.go.printer import FUNCTIONS as GO_FUNCTIONS
from engine.stacks.go.printer import ExprPrinter, strip_parens
from engine.stacks.go.text import go_string, import_block, raw_string
from engine.stacks.testindex import INDEX_PATH
from engine.tests.conftest import DFR, FSL, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
TEMPLATES = Path(__file__).resolve().parents[1] / "stacks" / "go" / "templates"
REPO_ROOT = Path(__file__).resolve().parents[2]


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
def go_designs(gmf_apps, gmf_app, gmf_processes, gmf_rules, gmf_findings):
    from engine.domains import graph as graph_mod

    graph = graph_mod.build_graph(list(gmf_apps.values()))
    return {pid: build_design_skeleton(gmf_processes[pid], gmf_app, gmf_rules[pid], gmf_findings[pid],
                                       graph_mod.io_card(gmf_processes[pid], gmf_app, graph), stack="go")
            for pid in PROCESS_IDS}


@pytest.fixture(scope="module")
def go_inputs(gmf_app, go_designs, gmf_rules, gmf_findings, gmf_scenarios):
    return gmf_app, PROCESS_IDS, go_designs, gmf_rules, gmf_findings, gmf_scenarios


@pytest.fixture(scope="module")
def go_solution(tmp_path_factory, go_inputs):
    return generate_go(*go_inputs, tmp_path_factory.mktemp("go") / "GmfCredit", Mirrors())


# ------------------------------------------------------------------ the brief's tests
def test_go_golden(go_solution):
    _golden("go/dealer_funding_request_repository.go", _file(go_solution, "dealer_funding_request_repository.go").content)
    _golden("go/gmf_credit_files.txt", "\n".join(f.path for f in go_solution.files) + "\n")


def test_go_deterministic(tmp_path, go_inputs, go_solution):
    again = generate_go(*go_inputs, tmp_path / "again", Mirrors())
    assert again.build_hash == go_solution.build_hash
    assert [(f.path, f.content, f.origin) for f in again.files] == \
        [(f.path, f.content, f.origin) for f in go_solution.files]


def test_every_mapping_row_exists_in_the_tree(go_solution, go_designs):  # spec criterion 3
    files = {f.path: f for f in go_solution.files}
    for d in go_designs.values():
        for r in d.mapping:
            f = files[f"{r.project}/{r.file}"]  # the row's own path, not only a suffix
            assert f"func (s *{r.class_name}) {r.method}(" in f.content or f"func {r.method}(" in f.content, \
                (r.file, r.class_name, r.method)


def test_untranslatable_step_keeps_tibco_and_placeholder(go_solution):  # Review Focus 1
    agent = [f for f in go_solution.files if f.origin == "agent" and not f.path.startswith("tests/")]
    tests = [f for f in go_solution.files if f.origin == "agent" and f.path.startswith("tests/")]
    assert tests and all("no test body for" in f.content for f in tests)  # rule tests wait for the Test Engineer
    assert agent and all('panic("agent fills:' in f.content and "// TIBCO" in f.content for f in agent)
    cond = _file(go_solution, "dealer_funding_request_conditions.go")
    assert cond.origin == "agent"
    # abs() is not an expression code writes: the TIBCO text stays above the placeholder
    assert re.search(r"// TIBCO condition: abs\(.*\n\s+panic\(\"agent fills: transition "
                     r"LookupDealToCheckExistingFunding\"\)", cond.content)
    # the conditions code can write are written
    assert 'tib.Compare(tib.Path(state.Get("LookupDealer"), "Record", 1, "status"), "==", tib.Of("ACTIVE"))' \
        in cond.content


def test_sql_verbatim_with_dollar_placeholders(go_solution, gmf_processes):
    repo = _file(go_solution, "dealer_funding_request_repository.go").content
    fsl = _file(go_solution, "funding_status_lookup_repository.go").content
    assert "where deal_id = $1" in repo and "where f.deal_id = $1" in fsl
    assert "values ($1, $2, $3, 'FUNDED', current_timestamp)" in repo
    assert "?" not in re.sub(r"(?s)//[^\n]*", "", repo + fsl).replace('"?"', "")
    for pid, text in ((DFR, repo), (FSL, fsl)):
        for act in gmf_processes[pid].activities:
            if act.type.startswith("jdbc."):
                for line in str(act.config["sql"]).replace("?", "$1").splitlines():
                    first = line.rstrip().split("$1")[0]
                    assert first in text, (act.name, line)


def test_runtime_contract_endpoints(go_solution):
    text = "\n".join(f.content for f in go_solution.files if f.path.endswith(".go"))
    for route in ('"GET /health"', '"POST /auth/dev-token"', '"POST /_bus/publish"', '"GET /_bus/messages"',
                  '"GET /_bus/dead-letters"'):
        assert route in text, route
    router = _file(go_solution, "internal/api/router.go").content
    assert 'mux.Handle("GET /funding/{dealId}", deps.Auth.Require(http.HandlerFunc(fundingStatusLookupHandler.Get)))' \
        in router
    settings = _file(go_solution, "internal/platform/settings.go").content
    assert 'd["CONNECTION_STRINGS_GMF_FUNDING_DB"] = "Host=localhost;' in settings  # development
    assert 'd["MESSAGING_MODE"] = "InMemory"' in settings and 'd["AUTH_MODE"] = "Production"' in settings


def test_go_sum_covers_go_mod_requirements(go_solution):
    mod = _file(go_solution, "go.mod").content
    lines = _file(go_solution, "go.sum").content.splitlines()
    required = re.findall(r"^\t(\S+) (v\S+)", mod, re.MULTILINE)
    assert len(required) == len(versions.DIRECT) + len(versions.INDIRECT)
    for module, version in required:
        assert sum(1 for ln in lines if ln.startswith((f"{module} {version} h1:", f"{module} {version}/go.mod h1:"))) == 2, module
    assert mod.startswith(f"module gmf-credit\n\ngo {versions.GO_MIN}\n") and "toolchain" not in mod


def test_no_public_proxy_or_em_dash(go_solution):
    blob = "\n".join(f.content for f in go_solution.files)
    assert "proxy.golang.org" not in blob and "sum.golang.org" not in blob and "—" not in blob and "{{" not in blob


def test_egress_audit_passes_on_templates_and_output(go_solution):
    """X11: no public host in the shipped templates or the generated project (GMF mirrors only)."""
    import egress_audit

    allow = egress_audit.load_allowlist(REPO_ROOT / "deploy" / "egress-allowlist.txt")
    assert egress_audit.audit_dirs([TEMPLATES, TEMPLATES.parent], allow) == []
    found = [(f.path, v.host) for f in go_solution.files
             for v in egress_audit.scan_text(Path("generated.txt"), f.content) if not allow.allows(v.host)]
    assert found == []


# ------------------------------------------------------------------ naming
def test_go_naming_members():
    n = GoNaming()
    assert n.type_name("dealer funding-request") == "DealerFundingRequest"
    assert n.method_name("", "LookupDealer") == n.method_name("", "Lookup Dealer") == "LookupDealer"
    assert n.method_name("Execute", "") == "Execute" and n.method_name("", "") == "Execute"
    assert n.method_name("Handle", "funding event") == "HandleFundingEvent"
    assert n.escape("type") == "type_" and n.escape("Type") == "Type"
    assert n.method_name("", "9 lives") == "Step9Lives"
    assert n.file_name("lookup dealer") == "lookup_dealer.go" and n.file_name("Lookup Test") == "lookup_test_go.go"
    assert n.exception_name("DealerInactiveFault") == "DealerInactiveFault" and n.exception_name("Timeout") == "TimeoutFault"
    assert n.config_key("dealerfunding", "DB_URL") == "GMF_DEALERFUNDING_DB_URL"
    assert n.config_key("dealerfunding", "GMF FundingDB", "password") == "GMF_DEALERFUNDING_GMF_FUNDING_DB_PASSWORD"
    assert n.connection_key("GMFFundingDB") == "CONNECTION_STRINGS_GMF_FUNDING_DB"
    assert [n.layer_folder(k, "GmfCredit") for k in ("api", "core", "infrastructure", "tests")] == [
        "internal/api", "internal/core", "internal/infrastructure", "tests"]
    assert n.contract_target("GmfCredit", "DealerFunding", "funding.request") == (
        "gmf-credit/internal/core/contracts/dealerfunding", "contracts/dealerfunding/funding_request.go")
    assert (n.platform, n.messaging_actor) == ("Go", "ServiceBus")
    words = {"app": "gmf.credit", "process": "DealerFundingRequest", "activity": "Lookup Dealer", "starter": "get",
             "resource": "EquifaxClient", "fault": "DealerInactiveFault", "target": "Audit Log",
             "destination": "gmf.funding.events"}
    assert n.row_names("jdbc.query", words) == ("dealer_funding_request_repository.go", "DealerFundingRequestRepository",
                                                "LookupDealer")
    assert n.row_names("http.invoke", words) == ("clients/equifax_client.go", "EquifaxClient", "LookupDealer")
    assert n.row_names("throw", words) == ("dealer_inactive_fault.go", "DealerInactiveFault", "NewDealerInactiveFault")
    assert n.row_names("jms.publish", words) == ("message_bus.go", "MessageBus", "Publish")
    assert n.row_names("input.mapping", words)[2] == "LookupDealerMapping"
    assert n.row_names("reply", words) == ("dealer_funding_request_handler.go", "DealerFundingRequestHandler", "Get")
    assert n.row_names("call.process", words)[:2] == ("audit_log_service.go", "AuditLogService")


def test_pattern_paths_cover_every_pattern_under_its_layer():
    n = GoNaming()
    for name, spec in PATTERNS.items():
        file, _cls, _method = n.pattern_paths(name)
        assert file.startswith(f"{spec.project}/") and file.endswith(".go"), name
    with pytest.raises(KeyError):
        n.pattern_paths("no.such.pattern")


def test_snake_and_env_keys_follow_the_platform_rule():
    assert [snake(x) for x in ("GMFFundingDB", "DbUrl", "DealerFundingRequest", "Gmf")] == [
        "gmf_funding_db", "db_url", "dealer_funding_request", "gmf"]
    assert env_key("FullyQualifiedNamespace") == "FULLY_QUALIFIED_NAMESPACE"
    assert module_path("GmfCredit") == "gmf-credit" and module_path("gmf.credit") == "gmf-credit"
    assert go_file("deploy_windows") == "deploy_windows_go.go" and go_file("dealer") == "dealer.go"
    config = (TEMPLATES / "config.go.j2").read_text(encoding="utf-8")
    assert "func Words(s string) []string" in config and "func Snake(segment string) string" in config


# ------------------------------------------------------------------ stack
def test_go_stack_is_registered_with_its_contract():
    stack = get_stack("go")
    assert isinstance(stack, GoStack) and isinstance(stack, StackGenerator)
    assert (stack.key, stack.label, stack.required_tools, stack.source_suffixes, stack.prompt_pack,
            stack.download_excluded, stack.service_process_marker) == (
        "go", "Go · net/http · go modules", ("go",), (".go",), "go", frozenset({".exiqo-bin", ".exiqo"}),
        ".exiqo-bin/server")
    assert stack_status("go", {"go": {"present": True}}, ["go"]).available is True
    assert stack_status("go", {}, ["go"]).reason == "toolchain missing: go"
    layout = stack.layout("GmfCredit")
    assert layout.solution_file == "go.mod" and layout.api_entry == ".exiqo-bin/server"
    assert layout.contract_file == CONTRACT_FILE and layout.test_root == "tests"
    assert layout.dockerfile_context["go_version"] == versions.GO_VERSION


def test_service_contract_and_test_index(go_solution):
    contract = json.loads(_file(go_solution, CONTRACT_FILE).content)
    assert contract == {"connections": ["GMFFundingDB"], "clients": [],
                        "secrets": [{"name": "ConnectionStrings--GMFFundingDB", "key": "ConnectionStrings__GMFFundingDB"}],
                        "plain_keys": [], "port_env": "PORT", "health": "/health"}
    index = json.loads(_file(go_solution, INDEX_PATH).content)
    assert index and all(e["path"].startswith("tests/") and e["name"].startswith("gmf-credit/tests.Test")
                         for e in index)


def test_layout_and_starters(go_solution):
    paths = {f.path for f in go_solution.files}
    for p in ("go.mod", "go.sum", "cmd/server/main.go", "internal/api/funding_status_lookup_handler.go",
              "internal/infrastructure/messaging/dealer_funding_request_consumer.go",
              "internal/core/contracts/dealerfunding/funding_request.go", "internal/platform/settings.go"):
        assert p in paths, p
    handler = _file(go_solution, "funding_status_lookup_handler.go").content
    assert 'r.PathValue("dealId")' in handler
    consumer = _file(go_solution, "dealer_funding_request_consumer.go").content
    assert 'const DealerFundingRequestDestination = "gmf.dealer.funding.request"' in consumer
    assert 'return properties["MsgType"] == "FUNDING_REQUEST"' in consumer
    assert 'input["Body"] = map[string]any{"FundingRequest": body}' in consumer
    service = _file(go_solution, "funding_status_lookup_service.go").content
    assert "return tib.ErrorReply(s.mappings.ReplyNotFoundMapping(state)), nil" in service
    assert "func (s *FundingStatusLookupService) Execute(ctx context.Context, state *tib.State) (tib.Result, error) {" \
        in service
    dfr = _file(go_solution, "dealer_funding_request_service.go").content
    assert ('if err := s.bus.Publish(ctx, "gmf.funding.events", tib.Path(s.mappings.PublishFundingEventMapping(state), '
            '"Body", "FundingEvent")); err != nil {') in dfr
    # a guard clause: the otherwise branch returns the fault, the conditional one carries on
    assert re.search(r"if !s\.conditions\.LookupDealerToLookupDeal\(state\) \{\n.*\n.*\n\s+return tib\.Result\{\}, "
                     r"NewDealerInactiveFault", dfr)
    services = _file(go_solution, "internal/core/services.go").content
    assert "s.DealerFundingRequest.repository = infrastructure.NewDealerFundingRequestRepository(db)" in services


def test_contract_structs(go_solution):
    text = _file(go_solution, "contracts/dealerfunding/funding_status_lookup_funding.go").content
    assert "package dealerfunding" in text
    assert "\t// TibcoPath: /FundingStatusResponse/fundingId\n\tFundingId *tib.Int `json:\"fundingId,omitempty\"`" in text
    assert "FundedAt *time.Time" in text and '"time"' in text


def test_contract_renderer_types():
    rec = ContractRecord("Loan", props=[
        ContractProp("Count", "count", "int", True, False, "count", tibco_path="/Loan/count"),
        ContractProp("Score", "score", "int", False, False, "score"),
        ContractProp("Notes", "notes", "string", False, True, "notes"),
        ContractProp("Status", "status", "Status", True, False, "status"),
        ContractProp("Amount", "amount", "decimal", True, False, "amount"),
    ], tibco_path="/Loan")
    model = ContractModel(records={"Loan": rec})
    model.enums["Status"] = ContractEnum("Status", ["ON-HOLD", "1st", "ON_HOLD"])
    text = go_contracts.render(model, "gmf-credit/internal/core/contracts/loans", "x.xsd")
    assert "package loans" in text and '"encoding/json"' in text
    assert "\t// TibcoPath: /Loan/count\n\tCount tib.Int `json:\"count\"`" in text
    assert "Score *tib.Int `json:\"score,omitempty\"`" in text and "Notes []tib.String" in text
    assert "Status Status `json:\"status\"`" in text and "Amount json.Number" in text
    assert '"gmf-credit/internal/tib"' in text  # the lenient scalars (X19: Jackson's coercions)
    assert '\tStatusOnHold  Status = "ON-HOLD"\n\tStatus1st     Status = "1st"\n\tStatusOnHold2 Status = "ON_HOLD"' in text


# ------------------------------------------------------------------ expressions and text
def test_every_xpath_function_has_a_go_helper():
    assert set(GO_FUNCTIONS) == set(FUNCTIONS)
    helpers = (TEMPLATES / "tib_text.go.j2").read_text(encoding="utf-8")
    for go in GO_FUNCTIONS.values():
        assert f"func {go.split('.')[1]}(" in helpers, go


def test_expression_printer():
    p = ExprPrinter({"LookupDeal": "LookupDeal", "get.parameters": "get.parameters"}, {"LookupDeal": ("Record",)})
    e = X.parse_simple("count($LookupDeal/Record) > 0 and not($get.parameters/tns:dealId = '')")
    assert p.cond(e) == ('(tib.Compare(tib.Of(tib.Count(tib.Path(state.Get("LookupDeal"), "Record"))), ">", '
                         'tib.Num("0")) && !(tib.Compare(tib.Path(state.Get("get.parameters"), "dealId"), "==", '
                         'tib.Of(""))))')
    assert p.value(X.Lit(Decimal("0.02"))) == 'tib.Num("0.02")'
    assert p.value(X.parse_simple("upper-case($get.parameters/x)")) == \
        'tib.UpperCase(tib.Path(state.Get("get.parameters"), "x"))'
    assert p.value(X.parse_simple("$_processContext/name")) is None
    assert p.value(X.parse_simple("$LookupDeal/status")) is None
    assert strip_parens("(a && (b))") == "a && (b)" and strip_parens("(a) && (b)") == "(a) && (b)"


def test_go_text_helpers():
    assert go_string('a"b\\c\n\x01') == '"a\\"b\\\\c\\n\\x01"'
    assert raw_string("select `x` from t") == '`select ` + "`" + `x` + "`" + ` from t`'
    assert import_block({"fmt", "context", "gmf-credit/internal/tib", "github.com/x/y"}, "gmf-credit") == [
        "import (", '\t"context"', '\t"fmt"', "", '\t"github.com/x/y"', '\t"gmf-credit/internal/tib"', ")"]
    text = 'package p\n\nimport (\n\t"context"\n\t"fmt"\n\n\t"gmf-credit/internal/tib"\n)\n\nfunc f(ctx context.Context) {}\n'
    assert prune_imports(text) == 'package p\n\nimport "context"\n\nfunc f(ctx context.Context) {}\n'
    # a name in a comment or a string is not a use
    assert '"fmt"' not in prune_imports(text.replace("{}", '{ _ = "fmt." } // fmt.Println'))


def test_the_whole_sample_application_generates(tmp_path, gmf_app):
    """Every gmf.credit process (queues, groups, fault handlers, timers, sub-process calls): it
    generates, each design row names code that exists, and each agent file keeps TIBCO text."""
    pids = [p.id for p in gmf_app.processes]
    sol = generate_go(gmf_app, pids, {}, {}, {}, [], tmp_path / "all", Mirrors())
    files = {f.path: f for f in sol.files}
    for d in sol.designs.values():
        assert d.stack == "go"
        for r in d.mapping:
            f = files[f"{r.project}/{r.file}"]
            assert f"func (s *{r.class_name}) {r.method}(" in f.content or f"func {r.method}(" in f.content, \
                (r.file, r.method)
    for f in sol.files:
        if f.origin == "agent" and f.path.startswith("tests/"):
            assert "no test body for" in f.content, f.path
        elif f.origin == "agent":
            assert 'panic("agent fills:' in f.content and "// TIBCO" in f.content, f.path
        if f.path.endswith(".go"):
            folder = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
            assert re.search(r"^package \w+$", f.content, re.MULTILINE), f.path
            if folder.startswith("internal/"):
                assert f"\npackage {folder.rsplit('/', 1)[-1]}\n" in "\n" + f.content, f.path
            code = GoLanguage().code_only(f.content)  # a brace in a rune or string literal is not a block
            assert code.count("{") == code.count("}"), f.path
    service = files["internal/core/nightly_delinquency_scan_service.go"].content
    assert 'range tib.Items(tib.Path(state.Get("FindDelinquentAccounts"), "Record"))' in service
    assert "internal/infrastructure/jobs/nightly_delinquency_scan_job.go" in files


def test_a_design_for_another_stack_is_refused(tmp_path, gmf_app, gmf_designs, gmf_rules, gmf_findings):
    with pytest.raises(ValueError, match="is for dotnet, not go"):
        generate_go(gmf_app, [DFR], gmf_designs, gmf_rules, gmf_findings, [], tmp_path / "x", Mirrors())


def test_contract_scalars_are_lenient_like_jackson():
    """X19 parity: a request body is checked the way Jackson reads it in the Java stack (a number
    for a string, "5" for an int, 12.0 for an int), still refusing real type errors."""
    lenient = (TEMPLATES / "lenient.go.j2").read_text(encoding="utf-8")
    for t in ("String", "Int", "Int64", "Float", "Boolean"):
        assert f"func (v *{t}) UnmarshalJSON(data []byte) error" in lenient, t
