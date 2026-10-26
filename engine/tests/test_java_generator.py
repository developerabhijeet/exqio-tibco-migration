"""The Java generator (target stacks, Task 7): Spring Boot 3.5 on Java 21 from the ServicePlan.

Golden files under ``engine/tests/golden/java/`` hold the repository of ``DealerFundingRequest`` and
the file list of ``gmf.credit``. Regenerate them after an intended change with
``UPDATE_GOLDEN=1 pytest engine/tests/test_java_generator.py`` and review the diff.
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
from engine.stacks.contracts import ContractModel, ContractProp, ContractRecord
from engine.stacks.java import JavaStack
from engine.stacks.java import contracts as java_contracts
from engine.stacks.java.generator import CONTRACT_FILE, SETTINGS_FILE, generate_java, prune_imports
from engine.stacks.java.naming import _WORD, JavaNaming, kebab, lower_camel
from engine.stacks.java.printer import FUNCTIONS as JAVA_FUNCTIONS
from engine.stacks.java.printer import ExprPrinter
from engine.stacks.java.text import java_string, sort_imports, text_block
from engine.stacks.testindex import INDEX_PATH
from engine.tests.conftest import DFR, FSL, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
TEMPLATES = Path(__file__).resolve().parents[1] / "stacks" / "java" / "templates"
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
def java_designs(gmf_apps, gmf_app, gmf_processes, gmf_rules, gmf_findings):
    from engine.domains import graph as graph_mod

    graph = graph_mod.build_graph(list(gmf_apps.values()))
    return {pid: build_design_skeleton(gmf_processes[pid], gmf_app, gmf_rules[pid], gmf_findings[pid],
                                       graph_mod.io_card(gmf_processes[pid], gmf_app, graph), stack="java")
            for pid in PROCESS_IDS}


@pytest.fixture(scope="module")
def java_inputs(gmf_app, java_designs, gmf_rules, gmf_findings, gmf_scenarios):
    return gmf_app, PROCESS_IDS, java_designs, gmf_rules, gmf_findings, gmf_scenarios


@pytest.fixture(scope="module")
def java_solution(tmp_path_factory, java_inputs):
    return generate_java(*java_inputs, tmp_path_factory.mktemp("java") / "GmfCredit", Mirrors())


# ------------------------------------------------------------------ the brief's tests
def test_java_golden(java_solution):
    _golden("java/DealerFundingRequestRepository.java", _file(java_solution, "DealerFundingRequestRepository.java").content)
    _golden("java/gmf_credit_files.txt", "\n".join(f.path for f in java_solution.files) + "\n")


def test_java_deterministic(tmp_path, java_inputs, java_solution):
    again = generate_java(*java_inputs, tmp_path / "again", Mirrors())
    assert again.build_hash == java_solution.build_hash
    assert [(f.path, f.content, f.origin) for f in again.files] == \
        [(f.path, f.content, f.origin) for f in java_solution.files]


def test_every_mapping_row_exists_in_the_tree(java_solution, java_designs):  # spec criterion 3
    files = {f.path: f for f in java_solution.files}
    for d in java_designs.values():
        for r in d.mapping:
            f = files[f"{r.project}/{r.file}"]  # the row's own path, not only a suffix
            assert f"class {r.class_name}" in f.content or f"record {r.class_name}" in f.content \
                or f"interface {r.class_name}" in f.content, (r.file, r.class_name)
            assert f" {r.method}(" in f.content, (r.file, r.method)


def test_untranslatable_step_keeps_tibco_and_placeholder(java_solution):  # Review Focus 1
    agent = [f for f in java_solution.files if f.origin == "agent" and not f.path.startswith("src/test/")]
    tests = [f for f in java_solution.files if f.origin == "agent" and f.path.startswith("src/test/")]
    assert tests and all("no test body for" in f.content for f in tests)  # rule tests wait for the Test Engineer
    assert agent and all('UnsupportedOperationException("agent fills:' in f.content and "// TIBCO" in f.content
                         for f in agent)
    cond = _file(java_solution, "DealerFundingRequestConditions.java")
    assert cond.origin == "agent"
    # abs() is not an expression code writes: the TIBCO text stays above the placeholder
    assert re.search(r"// TIBCO condition: abs\(.*\n\s+throw new UnsupportedOperationException\(\"agent fills: "
                     r"transition LookupDealToCheckExistingFunding\"\);", cond.content)
    # the conditions code can write are written
    assert 'TibValue.compare(TibValue.path(state.get("LookupDealer"), "Record", 1, "status"), "==", ' \
           'TibValue.of("ACTIVE"))' in cond.content


def test_sql_verbatim_with_jdbc_placeholders(java_solution, gmf_processes):
    repo = _file(java_solution, "DealerFundingRequestRepository.java").content
    fsl = _file(java_solution, "FundingStatusLookupRepository.java").content
    # The brief names "where f.deal_id = ?", which is FundingStatusLookup's statement; DFR's is checked too.
    assert "where deal_id = ?" in repo and "where f.deal_id = ?" in fsl
    assert "@p0" not in repo + fsl
    for pid, text in ((DFR, repo), (FSL, fsl)):
        for act in gmf_processes[pid].activities:
            if act.type.startswith("jdbc."):
                for line in str(act.config["sql"]).splitlines():
                    assert line.rstrip() in text, (act.name, line)


def test_runtime_contract_endpoints_and_env_binding(java_solution):
    text = "\n".join(f.content for f in java_solution.files if f.path.endswith(".java"))
    for route in ('"/health"', '"/auth/dev-token"', '"/_bus/publish"', '"/_bus/messages"', '"/_bus/dead-letters"'):
        assert route in text
    assert "class EnvBinding implements EnvironmentPostProcessor" in text
    factories = _file(java_solution, "META-INF/spring.factories").content
    assert factories.strip() == "org.springframework.boot.env.EnvironmentPostProcessor=com.gmf.gmfcredit.config.EnvBinding"
    yml = _file(java_solution, "application.yml").content
    assert "base-path: \"/\"" in yml and "include: \"health\"" in yml  # GET /health is the actuator
    assert "gmf-funding-db: \"Host=localhost;" in yml  # ConnectionStrings__GMFFundingDB in development


def test_no_public_repository_or_em_dash(java_solution):
    blob = "\n".join(f.content for f in java_solution.files)
    assert "repo.maven.apache.org" not in blob and "repo1.maven.org" not in blob and "—" not in blob and "{{" not in blob
    assert "<repositories>" not in blob and "<pluginRepositories>" not in blob


def test_egress_audit_passes_on_templates_and_output(java_solution):
    """X11: no public host in the shipped templates or the generated project (GMF mirrors only)."""
    import egress_audit

    allow = egress_audit.load_allowlist(REPO_ROOT / "deploy" / "egress-allowlist.txt")
    assert egress_audit.audit_dirs([TEMPLATES, TEMPLATES.parent], allow) == []
    found = [(f.path, v.host) for f in java_solution.files
             for v in egress_audit.scan_text(Path("generated.txt"), f.content) if not allow.allows(v.host)]
    assert found == []


def test_settings_without_mirror_has_no_mirror_block(java_solution):  # Review Focus 5 (dev half)
    assert "<mirror>" not in _file(java_solution, ".exiqo/maven-settings.xml").content


def test_settings_with_the_gmf_mirror(tmp_path, java_inputs):  # Review Focus 5 (GMF half)
    url = "https://artifacts.gmf.example/maven/all"
    sol = generate_java(*java_inputs, tmp_path / "m", Mirrors(maven=url))
    settings = _file(sol, SETTINGS_FILE).content
    assert f"<mirror>\n      <id>gmf</id>\n      <mirrorOf>*</mirrorOf>\n      <url>{url}</url>\n    </mirror>" in settings
    assert url not in _file(sol, "pom.xml").content  # the pom names no repository at all


# ------------------------------------------------------------------ naming
def test_java_naming_members():
    n = JavaNaming()
    assert n.type_name("dealer funding-request") == "DealerFundingRequest"
    assert n.method_name("", "LookupDealer") == n.method_name("", "Lookup Dealer") == "lookupDealer"
    assert n.method_name("Execute", "") == "execute" and n.method_name("", "") == "execute"
    assert n.method_name("Handle", "funding event") == "handleFundingEvent"
    assert n.method_name("", "new") == "new_" and n.escape("class") == "class_"
    assert n.method_name("", "9 lives") == "step9Lives"
    assert n.file_name("lookup dealer") == "LookupDealer.java"
    assert n.exception_name("DealerInactiveFault") == "DealerInactiveFaultException"
    assert n.config_key("dealerfunding", "DB_URL") == "gmf.dealerfunding.db-url"
    assert n.config_key("dealerfunding", "GMF FundingDB", "password") == "gmf.dealerfunding.gmf-funding-db.password"
    assert n.connection_key("GMFFundingDB") == "connection-strings.gmf-funding-db"
    assert [n.layer_folder(k, "GmfCredit") for k in ("api", "core", "infrastructure", "tests")] == [
        "src/main/java/com/gmf/gmfcredit/api", "src/main/java/com/gmf/gmfcredit/core",
        "src/main/java/com/gmf/gmfcredit/infrastructure", "src/test/java/com/gmf/gmfcredit"]
    assert n.contract_target("GmfCredit", "DealerFunding", "funding.request") == (
        "com.gmf.gmfcredit.core.contracts.dealerfunding", "contracts/dealerfunding/FundingRequestContract.java")
    assert (n.platform, n.config_file, n.messaging_actor) == ("Java", "application.yml", "ServiceBus")
    words = {"app": "gmf.credit", "process": "DealerFundingRequest", "activity": "Lookup Dealer", "starter": "get",
             "resource": "EquifaxClient", "fault": "DealerInactiveFault", "target": "Audit Log",
             "destination": "gmf.funding.events"}
    assert n.row_names("jdbc.query", words) == ("DealerFundingRequestRepository.java", "DealerFundingRequestRepository",
                                                "lookupDealer")
    assert n.row_names("http.invoke", words) == ("clients/EquifaxClient.java", "EquifaxClient", "lookupDealer")
    assert n.row_names("throw", words)[0] == "exceptions/DealerInactiveFaultException.java"
    assert n.row_names("jms.publish", words) == ("messaging/MessageBus.java", "MessageBus", "publish")
    assert n.row_names("input.mapping", words)[2] == "lookupDealerMapping"
    assert n.row_names("reply", words) == ("DealerFundingRequestController.java", "DealerFundingRequestController", "get")


def test_pattern_paths_cover_every_pattern_under_its_layer():
    n = JavaNaming()
    for name, spec in PATTERNS.items():
        file, _cls, _method = n.pattern_paths(name)
        assert file.startswith(f"{spec.project}/") and file.endswith(".java"), name
    with pytest.raises(KeyError):
        n.pattern_paths("no.such.pattern")
    assert n.pattern_paths("end")[0] == "core/{process}Service.java"
    assert n.pattern_paths("jdbc.query")[0] == "infrastructure/{process}Repository.java"
    assert n.pattern_paths("throw")[0] == "core/exceptions/{exception}.java"


def test_kebab_is_the_rule_env_binding_applies():
    assert [kebab(x) for x in ("GMFFundingDB", "DbUrl", "ConnectionStrings", "FullyQualifiedNamespace", "KeyVault",
                               "EquifaxClient", "Gmf")] == [
        "gmf-funding-db", "db-url", "connection-strings", "fully-qualified-namespace", "key-vault",
        "equifax-client", "gmf"]
    assert lower_camel("GMFFundingDB") == "gmfFundingDB" and lower_camel("URL") == "url"
    env = (TEMPLATES / "EnvBinding.java.j2").read_text(encoding="utf-8")
    assert f'Pattern.compile("{_WORD.pattern}")' in env  # one regex on both sides


# ------------------------------------------------------------------ stack
def test_java_stack_is_registered_with_its_contract():
    stack = get_stack("java")
    assert isinstance(stack, JavaStack) and isinstance(stack, StackGenerator)
    assert (stack.key, stack.label, stack.required_tools, stack.source_suffixes, stack.prompt_pack,
            stack.download_excluded, stack.service_process_marker) == (
        "java", "Java 21 · Spring Boot 3.5 · Maven", ("java", "mvn"), (".java",), "java",
        frozenset({"target/", ".exiqo/logs"}), "java")
    tools = {"java": {"present": True}, "mvn": {"present": True}}
    assert stack_status("java", tools, ["java"]).available is True
    assert stack_status("java", {"java": {"present": True}}, ["java"]).reason == "toolchain missing: mvn"
    layout = stack.layout("GmfCredit")
    assert layout.solution_file == "pom.xml" and layout.api_entry == "target/gmfcredit.jar"
    assert layout.contract_file == CONTRACT_FILE and layout.test_root == "src/test/java/com/gmf/gmfcredit"
    from engine.stacks.java.language import JavaLanguage
    from engine.stacks.java.runner import MavenRunner
    assert isinstance(stack.runner(Path(".")), MavenRunner) and isinstance(stack.language, JavaLanguage)


def test_service_contract_and_test_index(java_solution):
    contract = json.loads(_file(java_solution, CONTRACT_FILE).content)
    assert contract == {"connections": ["GMFFundingDB"], "clients": [],
                        "secrets": [{"name": "ConnectionStrings--GMFFundingDB", "key": "ConnectionStrings__GMFFundingDB"}],
                        "plain_keys": [], "port_env": None, "health": "/health"}
    index = json.loads(_file(java_solution, INDEX_PATH).content)
    assert index and all(e["path"].startswith("src/test/java/com/gmf/gmfcredit/") for e in index)


def test_layout_and_starters(java_solution):
    paths = {f.path for f in java_solution.files}
    main = "src/main/java/com/gmf/gmfcredit"
    for p in ("pom.xml", f"{main}/Application.java", f"{main}/api/FundingStatusLookupController.java",
              f"{main}/infrastructure/messaging/DealerFundingRequestConsumer.java",
              f"{main}/core/contracts/dealerfunding/FundingRequestContract.java", "src/main/resources/application.yml"):
        assert p in paths, p
    controller = _file(java_solution, "FundingStatusLookupController.java").content
    assert '@GetMapping("/funding/{dealId}")' in controller and '@PathVariable("dealId") String dealId' in controller
    consumer = _file(java_solution, "DealerFundingRequestConsumer.java").content
    assert 'DESTINATION = "gmf.dealer.funding.request"' in consumer
    assert '"FUNDING_REQUEST".equals(properties.get("MsgType"))' in consumer
    assert 'input.putObject("Body").set("FundingRequest", body);' in consumer
    service = _file(java_solution, "FundingStatusLookupService.java").content
    assert "return ProcessResult.error(FundingStatusLookupMappings.replyNotFoundMapping(state));" in service
    dfr = _file(java_solution, "DealerFundingRequestService.java").content
    assert 'bus.publish("gmf.funding.events", TibValue.path(DealerFundingRequestMappings.publishFundingEventMapping' \
           '(state), "Body", "FundingEvent"));' in dfr
    # a guard clause: the otherwise branch throws, the conditional one carries on
    assert re.search(r"if \(!DealerFundingRequestConditions\.lookupDealerToLookupDeal\(state\)\) \{\n.*\n.*\n\s+throw "
                     r"new DealerInactiveFaultException", dfr)


def test_contract_records(java_solution):
    text = _file(java_solution, "FundingStatusLookupFundingContract.java").content
    assert "public final class FundingStatusLookupFundingContract {" in text
    assert '@JsonProperty("fundingId") @TibcoPath("/FundingStatusResponse/fundingId") Integer fundingId' in text
    assert "OffsetDateTime fundedAt" in text and "return Masking.toMaskedString(this);" in text


def test_contract_renderer_types():
    rec = ContractRecord("Loan", props=[
        ContractProp("Count", "count", "int", True, False, "count", tibco_path="/Loan/count"),
        ContractProp("Score", "score", "int", False, False, "score"),
        ContractProp("Notes", "notes", "string", False, True, "notes"),
        ContractProp("Status", "status", "Status", True, False, "status"),
        ContractProp("Class", "class", "string", True, False, "class"),
        ContractProp("HashCode", "hashCode", "string", True, False, "hashCode"),
    ], tibco_path="/Loan")
    model = ContractModel(records={"Loan": rec})
    from engine.stacks.contracts import ContractEnum
    model.enums["Status"] = ContractEnum("Status", ["ON-HOLD", "1st", "ON_HOLD"])
    text = java_contracts.render(model, "com.gmf.app.core.contracts", "LoanContract", "x.xsd", "com.gmf.app")
    assert '@JsonProperty("count") @TibcoPath("/Loan/count") int count' in text
    assert "Integer score" in text and "List<String> notes" in text and "Status status" in text
    assert "String class_" in text and "String hashCode_" in text
    assert '@JsonProperty("ON-HOLD")\n        ON_HOLD,' in text and "V_1ST," in text and "ON_HOLD_2" in text
    clash = ContractRecord("LoanContract", props=[ContractProp("Next", "next", "LoanContract", False, False, "next")])
    text = java_contracts.render(ContractModel(records={"LoanContract": clash}), "p", "LoanContract", "x", "com.gmf.app")
    assert "public record LoanContractType(" in text and "LoanContractType next" in text


# ------------------------------------------------------------------ expressions and text
def test_every_xpath_function_has_a_java_helper():
    assert set(JAVA_FUNCTIONS) == set(FUNCTIONS)
    helpers = (TEMPLATES / "TibText.java.j2").read_text(encoding="utf-8")
    for java in JAVA_FUNCTIONS.values():
        assert f"public static JsonNode {java.split('.')[1]}(" in helpers, java


def test_expression_printer():
    p = ExprPrinter({"LookupDeal": "LookupDeal", "get.parameters": "get.parameters"}, {"LookupDeal": ("Record",)})
    e = X.parse_simple("count($LookupDeal/Record) > 0 and not($get.parameters/tns:dealId = '')")
    assert p.cond(e) == ('(TibValue.compare(TibValue.of(TibValue.count(TibValue.path(state.get("LookupDeal"), '
                         '"Record"))), ">", TibValue.of(0)) && !(TibValue.compare(TibValue.path('
                         'state.get("get.parameters"), "dealId"), "==", TibValue.of(""))))')
    assert p.value(X.Lit(Decimal("0.02"))) == 'TibValue.of(new BigDecimal("0.02"))'
    assert p.value(X.parse_simple("upper-case($get.parameters/x)")) == \
        'TibText.upperCase(TibValue.path(state.get("get.parameters"), "x"))'
    # a root the process does not have, or a JDBC output read without Record, is the agent's
    assert p.value(X.parse_simple("$_processContext/name")) is None
    assert p.value(X.parse_simple("$LookupDeal/status")) is None


def test_unknown_path_root_sends_mapping_and_condition_to_the_agent(tmp_path, gmf_app, java_designs, gmf_rules,
                                                                   gmf_findings):
    """The plan's agent flag is a lower bound: a shape code can write with a root no variable has
    still goes to the Code Builder, with its TIBCO text kept."""
    procs = {p.id: p for p in gmf_app.processes}
    dfr = procs[DFR]
    acts = []
    for a in dfr.activities:
        if a.name == "LookupDeal":
            b = a.input_bindings[0].model_copy(update={"expression": "$_processContext/dealId"})
            a = a.model_copy(update={"input_bindings": [b]})
        acts.append(a)
    links = [t.model_copy(update={"expression": "$NoSuchStep/x = 'y'"}) if t.id == "CheckExistingFundingToInsertFunding"
             else t for t in dfr.transitions]
    changed = dfr.model_copy(update={"activities": acts, "transitions": links})
    app = gmf_app.model_copy(update={"processes": [changed if p.id == DFR else p for p in gmf_app.processes]})
    sol = generate_java(app, [DFR], {DFR: java_designs[DFR]}, gmf_rules, gmf_findings, [], tmp_path / "x", Mirrors())
    mappings = _file(sol, "DealerFundingRequestMappings.java")
    assert mappings.origin == "agent"
    assert re.search(r"// TIBCO: jdbcQueryActivityInput/dealId = \$_processContext/dealId\n"
                     r"(\s+//.*\n)*\s+throw new UnsupportedOperationException\(\"agent fills: LookupDeal mapping\"\);",
                     mappings.content)
    cond = _file(sol, "DealerFundingRequestConditions.java").content
    assert "agent fills: transition CheckExistingFundingToInsertFunding" in cond


def test_java_text_helpers():
    assert java_string('a"b\\c\n') == '"a\\"b\\\\c\\n"'
    assert text_block('select \\d from "x"""', "    ") == '"""\n    select \\\\d from "x\\"""\n    """'
    assert text_block("a \nb", "  ") == '"""\n  a\\s\n  b\n  """'
    assert sort_imports("package p;\n\nimport org.b.B;\nimport com.a.A;\nimport static x.Y.z;\n\nclass C {}") == \
        "package p;\n\nimport static x.Y.z;\n\nimport com.a.A;\nimport org.b.B;\n\nclass C {}"
    assert prune_imports("package p;\n\nimport a.Used;\nimport a.Unused;\n\nclass C { Used u; }\n") == \
        "package p;\n\nimport a.Used;\n\nclass C { Used u; }\n"


def test_the_whole_sample_application_generates(tmp_path, gmf_app):
    """Every gmf.credit process (queues, groups, fault handlers, timers, sub-process calls): it
    generates, each design row names code that exists, and each agent file keeps TIBCO text."""
    pids = [p.id for p in gmf_app.processes]
    sol = generate_java(gmf_app, pids, {}, {}, {}, [], tmp_path / "all", Mirrors())
    files = {f.path: f for f in sol.files}
    for d in sol.designs.values():
        assert d.stack == "java"
        for r in d.mapping:
            f = files[f"{r.project}/{r.file}"]
            assert f" {r.method}(" in f.content and re.search(rf"\b(class|record|interface) {r.class_name}\b", f.content)
    for f in sol.files:
        if f.origin == "agent" and f.path.startswith("src/test/"):
            assert "no test body for" in f.content, f.path
        elif f.origin == "agent":
            assert 'UnsupportedOperationException("agent fills:' in f.content and "// TIBCO" in f.content, f.path
        if f.path.endswith(".java"):
            rel = f.path.split("/java/", 1)[1]
            assert f.content.startswith(f"package {'.'.join(rel.split('/')[:-1])};\n"), f.path
            assert re.search(rf"\b(class|record|interface|@interface|enum) {Path(f.path).stem}\b", f.content), f.path
            assert f.content.count("{") == f.content.count("}"), f.path
    service = files["src/main/java/com/gmf/gmfcredit/core/NightlyDelinquencyScanService.java"].content
    assert "for (JsonNode item : TibValue.items(TibValue.path(state.get(\"FindDelinquentAccounts\"), \"Record\")))" in service
    assert _file(sol, "NightlyDelinquencyScanJob.java").content.count("@Scheduled(fixedRateString") == 1


def test_a_design_for_another_stack_is_refused(tmp_path, gmf_app, gmf_designs, gmf_rules, gmf_findings):
    with pytest.raises(ValueError, match="is for dotnet, not java"):
        generate_java(gmf_app, [DFR], gmf_designs, gmf_rules, gmf_findings, [], tmp_path / "x", Mirrors())


# ------------------------------------------------------------------ Task 7 review fixes
def _with(gmf_app, pid, update):
    """A copy of the application whose process ``pid`` is changed by ``update(process)``."""
    app = gmf_app.model_copy(deep=True)
    update(next(p for p in app.processes if p.id == pid))
    return app


def test_a_topic_starter_receives_on_its_own_subscription(tmp_path, gmf_app):
    def topic(p):
        p.starter.config["messaging_style"] = "topic"

    sol = generate_java(_with(gmf_app, DFR, topic), [DFR], {}, {}, {}, [], tmp_path / "t", Mirrors())
    consumer = _file(sol, "DealerFundingRequestConsumer.java").content
    assert 'DESTINATION = "gmf.dealer.funding.request";' in consumer
    assert 'ENTITY = "gmf.dealer.funding.request/DealerFundingRequest";' in consumer  # as .NET and the design
    assert "bus.subscribe(ENTITY, this::handle)" in consumer
    tests = _file(sol, "DealerFundingRequestScenarioTests.java").content
    assert "DealerFundingRequestConsumer.ENTITY" in tests


def test_a_queue_starter_receives_on_the_queue(java_solution):
    consumer = _file(java_solution, "DealerFundingRequestConsumer.java").content
    assert 'ENTITY = "gmf.dealer.funding.request";' in consumer


def test_the_consumer_carries_jms_headers_and_application_properties(java_solution):
    consumer = _file(java_solution, "DealerFundingRequestConsumer.java").content
    assert 'input.set("JMSHeaders", MessageBus.jmsHeaders(message));' in consumer
    assert 'input.set("JMSProperties", TibValue.of(MessageBus.applicationProperties(message)));' in consumer
    bus = _file(java_solution, "core/messaging/MessageBus.java").content
    for header in ("JMSDestination", "JMSMessageID", "JMSCorrelationID", "JMSReplyTo", "JMSTimestamp"):
        assert f'"{header}"' in bus
    assert "SYSTEM_PROPERTIES = Set.of(REPLY_TO, CORRELATION_ID, MESSAGE_ID, ENQUEUED_TIME)" in bus
    memory = _file(java_solution, "InMemoryMessageBus.java").content
    assert "stamped.putIfAbsent(MESSAGE_ID" in memory and "stamped.putIfAbsent(ENQUEUED_TIME" in memory
    service_bus = _file(java_solution, "ServiceBusMessageBus.java").content
    assert "properties.putIfAbsent(MESSAGE_ID, message.getMessageId())" in service_bus


def test_mirror_url_is_xml_escaped_and_topic_names_are_java_strings(tmp_path, java_inputs):
    from engine.stacks.java.generator import render

    sol = generate_java(*java_inputs, tmp_path / "m", Mirrors(maven="https://mirror.gmf.example/m2?a=1&b=<2>"))
    settings = _file(sol, SETTINGS_FILE).content
    assert "<url>https://mirror.gmf.example/m2?a=1&amp;b=&lt;2&gt;</url>" in settings
    text = render("ServiceBusMessageBus.java.j2", pkg="p", app="A", app_id="a", topics=['gmf."x"', "t"])
    assert 'Set.of("gmf.\\"x\\"", "t")' in text


def test_key_vault_wins_over_the_environment():
    text = (TEMPLATES / "EnvBinding.java.j2").read_text()
    assert "sources.addBefore(StandardEnvironment.SYSTEM_ENVIRONMENT_PROPERTY_SOURCE_NAME,\n" \
           "                    new MapPropertySource(KEY_VAULT_SOURCE" in text


def test_nested_try_handlers_get_their_own_catch_variable(gmf_app, java_designs):
    from engine.stacks.java.printer import Ctx, JavaProcess
    from engine.stacks.plan import Seq, Try, build_plan

    proc = next(p for p in gmf_app.processes if p.id == DFR)
    plan = build_plan(proc, gmf_app, java_designs[DFR])
    jp = JavaProcess(Ctx(pkg="com.gmf.gmfcredit", naming=JavaNaming(), in_build={DFR: "DealerFundingRequestService"}),
                     proc, java_designs[DFR], plan, {})
    inner = Try(body=Seq(()), catch=(("Inner", Seq(())),), catch_all=None)
    outer = Try(body=Seq(()), catch=(("Outer", inner),), catch_all=None)
    text = "\n".join(jp.print_flow(outer, "direct").lines)
    assert "} catch (RuntimeException fault) {" in text and "} catch (RuntimeException fault2) {" in text
    assert 'ProcessFault.named(fault2, "Inner")' in text and "throw fault2;" in text and "throw fault;" in text
    assert jp.try_depth == 0


def test_commented_sketches_keep_one_comment_marker():
    from engine.stacks.java.printer import _commented

    assert _commented("    // TIBCO: x") == "//     TIBCO: x"
    assert _commented("state.set(a, b);") == "// state.set(a, b);"


def test_repository_methods_are_unique_with_a_warning(tmp_path, gmf_app, caplog):
    def clash(p):
        deal = next(a for a in p.activities if a.name == "LookupDeal")
        deal.name = "Lookup Dealer"  # gives lookupDealer, as LookupDealer does

    with caplog.at_level("WARNING"):
        sol = generate_java(_with(gmf_app, DFR, clash), [DFR], {}, {}, {}, [], tmp_path / "d", Mirrors())
    repo = _file(sol, "DealerFundingRequestRepository.java").content
    assert "public JsonNode lookupDealer(JsonNode input)" in repo and "public JsonNode lookupDealer2(JsonNode input)" in repo
    service = _file(sol, "DealerFundingRequestService.java").content
    assert ".lookupDealer(" in service and ".lookupDealer2(" in service
    assert any("repository method lookupDealer is taken" in r.message for r in caplog.records)


def test_review_fixes_on_the_whole_sample_application(tmp_path, gmf_app):
    pids = [p.id for p in gmf_app.processes]
    sol = generate_java(gmf_app, pids, {}, {}, {}, [], tmp_path / "all", Mirrors())
    assert not any("// //" in f.content for f in sol.files if f.path.endswith(".java"))
    job = _file(sol, "NightlyDelinquencyScanJob.java").content
    assert ('initialDelayString = "#{T(com.gmf.gmfcredit.infrastructure.jobs.NightlyDelinquencyScanJob)'
            ".initialDelayMs('${jobs.nightly-delinquency-scan.start:}')}\"") in job
    yml = _file(sol, "application.yml").content
    assert 'start: "02:00"' in yml
    assert "Jobs__NightlyDelinquencyScan__Start" in json.loads(_file(sol, CONTRACT_FILE).content)["plain_keys"]
    controller = _file(sol, "CreditApplicationIntakeController.java").content  # X19: the body is checked at the boundary
    assert "String invalid = TibValue.invalid(body, CreditApplicationIntakeApplicationsContract." in controller
    assert "ResponseEntity.badRequest()" in controller
    lookup = _file(sol, "FundingStatusLookupController.java").content
    assert "TibValue.invalid" not in lookup  # a GET has no body


def test_connection_string_keeps_the_ssl_mode(java_solution):
    src = _file(java_solution, "DataSources.java").content
    assert 'static String sslMode(Map<String, String> parts, String host)' in src
    assert 'sslmode=" + sslMode' in src  # the Npgsql form carries it into the JDBC URL
    assert 'case "require", "verifyfull", "verify-full":' in src and 'return trust ? "require" : "verify-full"' in src
    assert ".postgres.database.azure.com" in src
    assert 'uri.getRawQuery()' in src  # a postgresql:// URL keeps its own query (sslmode=...)


def test_verify_modes_use_the_jdk_trust_store(java_solution):
    """pgjdbc's default factory reads ~/.postgresql/root.crt: verify-ca/verify-full must name the JDK one."""
    src = _file(java_solution, "DataSources.java").content
    assert "org.postgresql.ssl.DefaultJavaSSLFactory" in src
    assert "static String trustStoreFor(String jdbcUrl)" in src
    # all three forms go through it
    assert src.count("trustStoreFor(") >= 4


def test_401_is_problem_json_like_go_and_python(java_solution):
    src = _file(java_solution, "SecurityConfig.java").content
    assert "application/problem+json" in src and "Unauthorized" in src and "WWW-Authenticate" in src
    assert "a bearer token is required" in src and "the bearer token is not valid" in src
    assert src.count("SecurityConfig::unauthorized") == 2  # the filter chain and the resource server
