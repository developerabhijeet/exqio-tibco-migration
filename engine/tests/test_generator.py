"""Solution generator, tests generator, TRX, coverage and metrics (Task 14).

Golden files under ``engine/tests/golden/`` hold the expected output for ``gmf.credit`` with
``dealerfunding.FundingStatusLookup`` and ``dealerfunding.DealerFundingRequest``. Regenerate them
after an intended generator change with ``UPDATE_GOLDEN=1 pytest engine/tests/test_generator.py``
and review the diff.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import warnings
from pathlib import Path

import pytest

from engine.analysis.models import Citation, Rule
from engine.analysis.static_rules import run_static_rules
from engine.domains import graph as graph_mod
from engine.dotnet_gen import coverage, generator, metrics, tests_gen, trx
from engine.dotnet_gen.design import build_design_skeleton
from engine.dotnet_gen.runner import BuildResult, TestRun
from engine.dotnet_gen.trx import TestCase
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
GOLDEN = Path(__file__).resolve().parent / "golden"
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://exiqo:exiqo@localhost:5432/exiqo_test")
PROCESS_IDS = ["dealerfunding.FundingStatusLookup", "dealerfunding.DealerFundingRequest"]
DFR = "dealerfunding.DealerFundingRequest"
FSL = "dealerfunding.FundingStatusLookup"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def _cit(activity: str, kind: str, text: str) -> Citation:
    return Citation(activity_id=activity, kind=kind, text=text)


def hand_rules() -> dict[str, list[Rule]]:
    """Approved rules written by hand so the ids and texts are stable for the golden files."""
    dfr = [
        Rule(id="R1", text="A dealer must be ACTIVE to receive funding.", confidence=0.95,
             status="approved",
             citations=[_cit("LookupDealer", "condition", "$LookupDealer/Record[1]/status = 'ACTIVE'")]),
        Rule(id="R2", text="The funding amount must be within 2% of the approved deal amount.",
             confidence=0.9, status="approved",
             citations=[_cit("LookupDeal", "condition", "number($LookupDeal/Record[1]/approved_amount) * 0.02")]),
        Rule(id="R3", text="A deal is funded at most once; a repeat request is logged as DF-409.",
             confidence=0.85, status="edited",
             citations=[_cit("CheckExistingFunding", "sql", "select funding_id, status"),
                        _cit("InsertFunding", "sql", "insert into gmf.funding")]),
        Rule(id="R4", text="Draft rule that is not approved and gets no test.", confidence=0.4,
             status="draft", citations=[_cit("LookupDeal", "sql", "from gmf.deal")]),
    ]
    fsl = [
        Rule(id="R5", text="Funding status is looked up by deal id.", confidence=0.9, status="approved",
             citations=[_cit("QueryFundingStatus", "sql", "where f.deal_id = ?")]),
        Rule(id="R6", text="An unknown deal returns 404 \"No funding for deal\".", confidence=0.9,
             status="approved",
             citations=[_cit("ReplyNotFound", "expression", 'concat("No funding for deal ", $get.parameters/tns:dealId)')]),
    ]
    return {DFR: dfr, FSL: fsl}


@pytest.fixture(scope="module")
def apps():
    return {a.id: a for a in scan_folder(GMF).applications}


@pytest.fixture(scope="module")
def app(apps):
    return apps["gmf.credit"]


@pytest.fixture(scope="module")
def processes(app):
    return {p.id: p for p in app.processes}


@pytest.fixture(scope="module")
def findings(app, processes):
    out = {}
    for pid in PROCESS_IDS:
        found = run_static_rules(processes[pid], app.contracts, app.substvars)
        out[pid] = [f.model_copy(update={"decision": "keep"}) for f in found]
    return out


@pytest.fixture(scope="module")
def rules():
    return hand_rules()


@pytest.fixture(scope="module")
def designs(apps, app, processes, rules, findings):
    graph = graph_mod.build_graph(list(apps.values()))
    return {pid: build_design_skeleton(processes[pid], app, rules[pid], findings[pid],
                                       graph_mod.io_card(processes[pid], app, graph))
            for pid in PROCESS_IDS}


@pytest.fixture(scope="module")
def scenarios(app, processes):
    from engine.parity.db import ScenarioDB
    from engine.parity.evaluator import Evaluator
    from engine.parity.scenarios import fill_expected, generate_scenarios
    from engine.parity.stubs import WireMockStubs

    db = ScenarioDB(DSN)
    db.reset(GMF / "Database" / "schema.sql", GMF / "Database" / "seed.sql")
    evaluator = Evaluator(app, db, WireMockStubs.from_folder(GMF / "Stubs"))
    out = []
    try:
        for pid in PROCESS_IDS:
            static = generate_scenarios(processes[pid], app.contracts, app)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out += asyncio.run(fill_expected(static, evaluator))
    finally:
        db.close()
    return out


@pytest.fixture(scope="module")
def solution(tmp_path_factory, app, designs, rules, findings, scenarios):
    out = tmp_path_factory.mktemp("gen") / "GmfCredit"
    return generator.generate_solution(app, PROCESS_IDS, designs, rules, findings, scenarios, out)


def _file(solution, suffix: str) -> generator.GenFile:
    hits = [f for f in solution.files if f.path.endswith(suffix)]
    assert len(hits) == 1, (suffix, [f.path for f in hits])
    return hits[0]


def _golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"golden file {path} missing; run with UPDATE_GOLDEN=1"
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from the golden file"


# ------------------------------------------------------------------ golden
def test_generate_solution_golden(solution):
    assert solution.name == "GmfCredit"
    listing = "\n".join(sorted(f.path for f in solution.files)) + "\n"
    _golden("gmf_credit_files.txt", listing)
    _golden("Directory.Build.props", _file(solution, "/Directory.Build.props".lstrip("/")).content)
    _golden("DealerFundingRequestRepository.cs",
            _file(solution, "Repositories/DealerFundingRequestRepository.cs").content)
    _golden("DealerFundingRequestRulesTests.cs",
            _file(solution, "DealerFundingRequestRulesTests.cs").content)
    for f in solution.files:  # every file is on disk with the same content
        assert (solution.root / f.path).read_text(encoding="utf-8") == f.content


def test_generation_is_deterministic(tmp_path, app, designs, rules, findings, scenarios, solution):
    again = generator.generate_solution(app, PROCESS_IDS, designs, rules, findings, scenarios, tmp_path / "x")
    assert again.build_hash == solution.build_hash
    assert re.fullmatch(r"[0-9a-f]{64}", solution.build_hash)


def test_solution_layout_and_projects(solution):
    paths = {f.path for f in solution.files}
    for p in ("GmfCredit.sln", "Directory.Build.props", "Directory.Packages.props", "nuget.config",
              "src/GmfCredit.Api/GmfCredit.Api.csproj", "src/GmfCredit.Core/GmfCredit.Core.csproj",
              "src/GmfCredit.Infrastructure/GmfCredit.Infrastructure.csproj",
              "tests/GmfCredit.Tests/GmfCredit.Tests.csproj",
              "src/GmfCredit.Api/Program.cs", "src/GmfCredit.Api/appsettings.json",
              "src/GmfCredit.Api/appsettings.Development.json",
              "src/GmfCredit.Api/Controllers/FundingStatusLookupController.cs",
              "src/GmfCredit.Core/Messaging/IMessageBus.cs",
              "src/GmfCredit.Infrastructure/Messaging/InMemoryMessageBus.cs",
              "src/GmfCredit.Infrastructure/Messaging/ServiceBusMessageBus.cs",
              "src/GmfCredit.Infrastructure/Messaging/DealerFundingRequestConsumer.cs",
              "src/GmfCredit.Core/Contracts/TibcoPathAttribute.cs",
              "src/GmfCredit.Core/Exceptions/DealerInactiveFaultException.cs",
              "tests/GmfCredit.Tests/TestDatabase.cs",
              "tests/GmfCredit.Tests/Database/schema.sql", "tests/GmfCredit.Tests/Database/seed.sql"):
        assert p in paths, p
    for reserved in generator.RESERVED_FOR_INFRA:
        assert not any(p == reserved or p.startswith(reserved) for p in paths), reserved
    sln = _file(solution, "GmfCredit.sln").content
    for proj in ("GmfCredit.Api", "GmfCredit.Core", "GmfCredit.Infrastructure", "GmfCredit.Tests"):
        assert f'"{proj}"' in sln
    props = _file(solution, "Directory.Build.props").content
    for token in ("<TargetFramework>net10.0</TargetFramework>", "<Nullable>enable</Nullable>",
                  "<TreatWarningsAsErrors>false</TreatWarningsAsErrors>",
                  "<EnableNETAnalyzers>true</EnableNETAnalyzers>", "<AnalysisLevel>latest</AnalysisLevel>",
                  "RestoreAdditionalProjectSources"):
        assert token in props
    core = _file(solution, "GmfCredit.Core.csproj").content
    assert "PackageReference" not in core and "FrameworkReference" not in core  # BCL only
    packages = _file(solution, "Directory.Packages.props").content
    for pkg in ("Dapper", "Npgsql", "Swashbuckle.AspNetCore", "xunit", "Microsoft.NET.Test.Sdk",
                "Testcontainers.PostgreSql", "Azure.Messaging.ServiceBus", "coverlet.collector",
                "Microsoft.AspNetCore.Authentication.JwtBearer", "Azure.Identity",
                "Azure.Extensions.AspNetCore.Configuration.Secrets"):
        assert f'Include="{pkg}"' in packages, pkg
    layout = generator.solution_layout("GmfCredit")
    assert layout["api_project"] == "src/GmfCredit.Api/GmfCredit.Api.csproj" and layout["api_dll"] == "GmfCredit.Api.dll"
    for proj in layout["projects"].values():
        assert proj["csproj"] in paths and proj["csproj"].startswith(proj["folder"] + "/")
    assert layout["solution"] in paths


def test_process_id_on_per_process_files(solution):
    by = {f.path: f for f in solution.files}
    for suffix, pid in (("Services/DealerFundingRequestService.cs", DFR), ("Services/DealerFundingRequestState.cs", DFR),
                        ("Mappings/DealerFundingRequestMappings.cs", DFR), ("Rules/DealerFundingRequestConditions.cs", DFR),
                        ("Data/DealerFundingRequestData.cs", DFR), ("Repositories/DealerFundingRequestRepository.cs", DFR),
                        ("Messaging/DealerFundingRequestConsumer.cs", DFR), ("Exceptions/DealerInactiveFaultException.cs", DFR),
                        ("DealerFundingRequestRulesTests.cs", DFR), ("DealerFundingRequestBranchTests.cs", DFR),
                        ("DealerFundingRequestScenarioTests.cs", DFR), ("Fixtures/DealerFundingRequest/happy.json", DFR),
                        ("Controllers/FundingStatusLookupController.cs", FSL), ("Services/FundingStatusLookupService.cs", FSL)):
        assert _file(solution, suffix).process_id == pid, suffix
    for shared in ("src/GmfCredit.Api/Program.cs", "Directory.Build.props", "GmfCredit.sln",
                   "src/GmfCredit.Infrastructure/Messaging/ServiceBusMessageBus.cs",
                   "src/GmfCredit.Infrastructure/InfrastructureServiceCollectionExtensions.cs",
                   "tests/GmfCredit.Tests/TestDatabase.cs", "src/GmfCredit.Api/appsettings.json"):
        assert by[shared].process_id is None, shared


def test_no_em_dash_and_no_unresolved_template(solution):
    for f in solution.files:
        assert "—" not in f.content, f.path
        assert "{{" not in f.content or f.path.endswith(".json"), f.path
        assert "{%" not in f.content, f.path
        assert "@Microsoft.KeyVault" not in f.content, f.path  # secrets arrive through the Key Vault provider
        assert "ConfigureAwait" not in f.content, f.path


def test_sql_verbatim_in_repository(solution, processes):
    repo = _file(solution, "Repositories/DealerFundingRequestRepository.cs").content
    for act in processes[DFR].activities:
        if not act.type.startswith("jdbc."):
            continue
        sql = act.config["sql"]
        literal = generator.raw_string_literal(generator.to_named_parameters(sql), indent=4)
        assert literal in repo, act.id
        # converting back gives the TIBCO statement exactly
        named = generator.to_named_parameters(sql)
        assert re.sub(r"@p\d+", "?", named) == sql
        assert f"TIBCO: {act.name} line {act.source_line}" in repo
    assert "Rules: R1. Findings: S2-DealerFundingRequest-1 (kept). TIBCO: LookupDealer line 1014" in repo
    assert "@p0 = dealerId" in repo  # documented binding order
    assert "TIBCO SQL: <c>select dealer_id, dealer_name, status from gmf.dealer where dealer_id like ?</c>" in repo
    assert "TIBCO maxRows 100" in repo


def test_named_parameters_skip_literals_and_comments():
    sql = "select '?', \"a?\" from t -- why?\n where a = ? and b = ? /* ? */ and c ?| array['x']"
    assert generator.to_named_parameters(sql) == (
        "select '?', \"a?\" from t -- why?\n where a = @p0 and b = @p1 /* ? */ and c ?| array['x']")
    assert generator.to_named_parameters("x like '%' || ?") == "x like '%' || @p0"
    # PgJDBC escapes the jsonb ? operator as ??; Npgsql wants the single operator
    assert generator.to_named_parameters("select 1 where doc ?? 'k' and id = ?") == "select 1 where doc ? 'k' and id = @p0"


def test_dev_token_endpoint_only_in_development(solution):
    program = _file(solution, "src/GmfCredit.Api/Program.cs").content
    assert program.count('"/auth/dev-token"') == 1
    idx = program.index('"/auth/dev-token"')
    before = program[:idx]
    assert before.rfind("#if EXIQO_DEV_ENDPOINTS") > before.rfind("#endif")  # compiled out in Release
    assert "if (devAuth)" in before[before.rfind("#if EXIQO_DEV_ENDPOINTS"):]
    assert "var devAuth = builder.Environment.IsDevelopment() && devAuthRequested;" in program  # allow-list
    assert '"/_bus/publish"' in program and '"/health"' in program and "UseSwaggerUI" in program
    assert 'var swagger = builder.Environment.IsDevelopment() || builder.Configuration.GetValue("Swagger:Enabled", false);' in program
    assert "if (swagger)\n{\n    app.UseSwagger();" in program
    assert "JwtBearer" in program and "Auth:Mode" in program
    # /_bus/publish answers one object with the disposition as a string; /_bus/messages stays a list
    publish = program[program.index('"/_bus/publish"'):program.index('"/_bus/messages"')]
    for disposition in ('"completed"', '"dead_lettered"', '"abandoned"'):
        assert f"new BusPublishResult({disposition}" in publish
    devtoken = _file(solution, "src/GmfCredit.Api/Auth/DevToken.cs").content
    assert '[property: JsonPropertyName("messages_published")] int MessagesPublished' in devtoken
    assert '[property: JsonPropertyName("disposition")] string Disposition' in devtoken
    assert "Results.Ok(entity is null ? bus.Published : bus.PublishedTo(entity))" in program
    # CORS for the web Verify tab, Development only
    cors = program[program.index("const string DevelopmentCors"):]
    assert cors.index("if (builder.Environment.IsDevelopment())") < cors.index("AddCors(")
    assert 'GetSection("Cors:Origins")' in program and '"http://localhost:3000"' in program
    assert "if (app.Environment.IsDevelopment())\n{\n    app.UseCors(DevelopmentCors);\n}" in program
    assert program.index("app.UseCors(DevelopmentCors)") < program.index("app.UseAuthentication()")
    api = _file(solution, "GmfCredit.Api.csproj").content
    assert "'$(Configuration)' != 'Release'" in api and "EXIQO_DEV_ENDPOINTS" in api
    prod = json.loads(_file(solution, "src/GmfCredit.Api/appsettings.json").content)
    dev = json.loads(_file(solution, "src/GmfCredit.Api/appsettings.Development.json").content)
    assert prod["Auth"]["Mode"] == "Production" and "DevToken" not in prod["Auth"]
    assert set(prod["Auth"]["EntraId"]) >= {"Instance", "TenantId", "Audience"}
    assert dev["Auth"]["Mode"] == "Development" and len(dev["Auth"]["DevToken"]["SigningKey"]) >= 32
    assert dev["Messaging"]["Mode"] == "InMemory" and prod["Messaging"]["Mode"] == "ServiceBus"
    assert dev["Cors"]["Origins"] == ["http://localhost:3000"] and "Cors" not in prod


def test_configuration_contract_key_vault_and_service_bus(solution):
    prod = json.loads(_file(solution, "src/GmfCredit.Api/appsettings.json").content)
    dev = json.loads(_file(solution, "src/GmfCredit.Api/appsettings.Development.json").content)
    program = _file(solution, "src/GmfCredit.Api/Program.cs").content
    di = _file(solution, "InfrastructureServiceCollectionExtensions.cs").content
    # production: non-secret keys only, Key Vault and Service Bus by placeholder
    assert prod["KeyVault"]["Uri"].startswith("https://<key-vault-name>")
    assert prod["Messaging"] == {"Mode": "ServiceBus",
                                 "ServiceBus": {"FullyQualifiedNamespace": "<service-bus-namespace>.servicebus.windows.net"}}
    assert "ConnectionStrings" not in prod and "Gmf" not in prod  # secrets from Key Vault; EMS/JDBC substvars dropped
    # development: local values, Key Vault off
    assert dev["KeyVault"]["Uri"] == ""
    assert dev["ConnectionStrings"]["GMFFundingDB"].startswith("Host=localhost;Port=5432;Database=gmf;")
    # Program.cs adds Key Vault with DefaultAzureCredential and documents the secrets and dropped substvars
    assert "builder.Configuration.AddAzureKeyVault(new Uri(keyVaultUri), new DefaultAzureCredential());" in program
    assert "//     Key Vault secret ConnectionStrings--GMFFundingDB -> ConnectionStrings:GMFFundingDB" in program
    assert "//     EMS.PROVIDER.URL: JMS connection setting: replaced by Azure Service Bus (Messaging:*)" in program
    assert "//     GMF.DB.URL: JDBC setting: replaced by ConnectionStrings:GMFFundingDB" in program
    # Service Bus through managed identity, no connection string
    assert 'configuration["Messaging:ServiceBus:FullyQualifiedNamespace"]' in di and "new DefaultAzureCredential()" in di
    assert 'GetConnectionString("ServiceBus")' not in di


def test_mappings_keep_tibco_expressions(solution):
    f = _file(solution, "Mappings/DealerFundingRequestMappings.cs")
    text = f.content
    assert f.origin == "agent"
    # simple path generated by code, with the expression as a comment above it
    assert "// TIBCO: DealerInactiveFault/dealerId = $ReceiveFundingRequest/Body/tns:FundingRequest/tns:dealerId" in text
    # same types: a direct assignment, no conversion helper
    assert "DealerId = state.Input.Message.DealerId," in text
    assert "Status = state.LookupDealer.FirstOrDefault()?.Status," in text
    assert 'Message = "Dealer is not active for funding",' in text
    assert "Amount = state.Input.Message.FundingAmount," in text
    # agent row: expression kept, body throws
    m = re.search(r"LookupDealerMapping\(DealerFundingRequestState state\)\n    \{\n(.*?)\n    \}", text, re.DOTALL)
    assert m, text
    assert "concat(normalize-space($ReceiveFundingRequest/Body/tns:FundingRequest/tns:dealerId), '%')" in m.group(1)
    assert 'throw new NotImplementedException("agent fills: LookupDealer");' in m.group(1)
    fsl = _file(solution, "Mappings/FundingStatusLookupMappings.cs").content
    assert "DealId = state.Input.Parameters.DealId," in fsl
    assert "FundingId = state.QueryFundingStatus.FirstOrDefault()?.FundingId," in fsl


def test_service_controller_consumer(solution):
    svc = _file(solution, "Services/FundingStatusLookupService.cs").content
    # straight-line: query, guard clause for the otherwise link, reply
    assert "ProcessFlow" not in svc and "switch" not in svc
    assert ("        if (!FundingStatusLookupConditions.QueryFundingStatusTogetOut(state))\n        {\n"
            "            // Link QueryFundingStatusToReplyNotFound: otherwise\n") in svc
    assert "return ProcessResult.FromError(FundingStatusLookupMappings.ReplyNotFoundMapping(state));" in svc
    assert svc.rstrip().endswith("return new ProcessResult(200, FundingStatusLookupMappings.GetOutMapping(state));\n    }\n}")
    dfr_svc = _file(solution, "Services/DealerFundingRequestService.cs").content
    assert "ProcessFlow" not in dfr_svc
    assert "throw new DealerInactiveFaultException(DealerFundingRequestMappings.ThrowDealerInactiveMapping(state));" in dfr_svc
    cond = _file(solution, "Rules/FundingStatusLookupConditions.cs").content
    assert "return state.QueryFundingStatus.Count > 0;" in cond
    ctl = _file(solution, "Controllers/FundingStatusLookupController.cs").content
    assert '[HttpGet("/funding/{dealId}")]' in ctl and "[Authorize]" in ctl
    consumer = _file(solution, "Messaging/DealerFundingRequestConsumer.cs").content
    assert 'public const string Entity = "gmf.dealer.funding.request";' in consumer
    assert 'public const string SelectorFilter = "MsgType = \'FUNDING_REQUEST\'";' in consumer
    assert 'message.Properties.TryGetValue("MsgType", out var msgType)' in consumer
    assert 'return BusOutcome.DeadLetter("SelectorMismatch", SelectorFilter);' in consumer  # queue: never completed
    assert "catch (ProcessFaultException fault)" in consumer
    assert "return BusOutcome.DeadLetter(fault.FaultName, fault.Message);" in consumer  # business fault: no retry
    assert "await _subscription.DisposeAsync();" in consumer  # StopAsync stops the processor
    assert "auto (acknowledged on receipt; a failed job was not redelivered)" in consumer
    bus = _file(solution, "ServiceBusMessageBus.cs").content
    assert "AutoCompleteMessages = false" in bus and "DeadLetterMessageAsync" in bus and "AbandonMessageAsync" in bus
    assert 'logger.LogError(args.Exception, "Service Bus error on {EntityPath} from {ErrorSource}", args.EntityPath, args.ErrorSource);' in bus
    assert "await processor.StopProcessingAsync();" in bus and "JMS redelivery" not in bus
    exc = _file(solution, "Exceptions/DealerInactiveFaultException.cs").content
    assert "public sealed class DealerInactiveFaultException" in exc and "DealerInactiveFault" in exc
    dfr = _file(solution, "Rules/DealerFundingRequestConditions.cs")
    assert dfr.origin == "agent"  # the 2% tolerance condition is left to the builder
    assert 'return state.LookupDealer.FirstOrDefault()?.Status == "ACTIVE";' in dfr.content
    tib = _file(solution, "Tibco/TibValue.cs").content
    assert "implicit operator" not in tib and "public decimal? AsDecimal()" in tib


@pytest.fixture(scope="module")
def estate(tmp_path_factory, apps, app):
    """CreditApplicationIntake (SSN fields), BureauOrchestrator (parallel links and a join) and
    NightlyDelinquencyScan (timer), generated without scenarios."""
    graph = graph_mod.build_graph(list(apps.values()))
    procs = {p.id: p for p in app.processes}
    pids = ["creditorigination.CreditApplicationIntake", "creditorigination.BureauOrchestrator",
            "loanservicing.NightlyDelinquencyScan"]
    designs = {pid: build_design_skeleton(procs[pid], app, [], [], graph_mod.io_card(procs[pid], app, graph))
               for pid in pids}
    return generator.generate_solution(app, pids, designs, {}, {}, [], tmp_path_factory.mktemp("estate") / "G")


def test_sensitive_fields_masked(estate):
    bureau = _file(estate, "Contracts/CreditOrigination/Bureau.cs").content
    block = bureau[bureau.index("public sealed record BureauRequest"):]
    block = block[: block.index("\n}\n")]
    assert '    [Sensitive]\n    [JsonPropertyName("ssn")]' in block
    assert "public override string ToString() => Masking.ToMaskedString(this);" in block
    data = _file(estate, "Data/CreditApplicationIntakeData.cs").content
    assert "using GmfCredit.Core.Contracts;" in data and "[Sensitive]\n    public string? Ssn { get; init; }" in data
    assert "Masking.MaskText(entry.Message)" in _file(estate, "LoggerStepLogger.cs").content
    assert 'details.Extensions["fault"] = Masking.MaskJson(fault.FaultData);' in _file(estate, "FaultExceptionHandler.cs").content


def test_queue_form_for_parallel_links(estate):
    svc = _file(estate, "Services/BureauOrchestratorService.cs").content
    assert "var flow = new ProcessFlow(Links);" in svc
    assert 'new("ParseEquifaxToMergeScores", "ParseEquifax", "MergeScores"),' in svc
    assert "flow.Complete(" in svc and "pending.Enqueue" not in svc
    assert "join" in svc.split("<remarks>", 1)[1].split("</remarks>", 1)[0]


def test_timer_job_never_throws(estate):
    job = _file(estate, "Jobs/NightlyDelinquencyScanJob.cs").content
    assert "TimeProvider timeProvider, ILogger<NightlyDelinquencyScanJob> logger" in job
    assert "new PeriodicTimer(interval, timeProvider)" in job and "Task.Delay(first - now, timeProvider, stoppingToken)" in job
    assert "catch (Exception ex) when (ex is not OperationCanceledException)" in job  # each tick
    assert 'logger.LogError(ex, "NightlyDelinquencyScan timer stopped");' in job  # nothing escapes ExecuteAsync


def test_tests_generated_first(solution, processes, scenarios):
    rules_cs = _file(solution, "DealerFundingRequestRulesTests.cs").content
    assert rules_cs.count("[Fact(") == 3  # approved and edited rules only
    assert '[Fact(DisplayName = "R1 A dealer must be ACTIVE to receive funding.")]' in rules_cs
    assert 'Assert.Fail("no test body for R1");' in rules_cs and "R4" not in rules_cs
    assert "public Task R1_a_dealer_must_be_active_to_receive_funding()" in rules_cs
    assert "public Task R2_the_funding_amount_must_be_within_2_of_the_approved_deal_amount()" in rules_cs
    for name in re.findall(r"public Task (\w+)\(\)", rules_cs + _file(solution, "DealerFundingRequestBranchTests.cs").content):
        assert len(name) <= 80, name
    branch = _file(solution, "DealerFundingRequestBranchTests.cs").content
    branches = tests_gen.branch_transitions(processes[DFR])
    assert len(branches) == 6
    assert branch.count("[Fact(") == len(branches)
    for t in branches:
        assert f"[{t.id}]" in branch
    sc = _file(solution, "DealerFundingRequestScenarioTests.cs").content
    dfr_scenarios = [s for s in scenarios if s.process_id == DFR]
    assert dfr_scenarios and sc.count("[Fact(") == len(dfr_scenarios)
    for s in dfr_scenarios:
        fixture = _file(solution, tests_gen.fixture_path(processes[DFR], s))
        data = json.loads(fixture.content)
        assert data["scenario_id"] == s.id and data["input"] == s.input
        assert data["label"] == "derived from the TIBCO model"
    fsl = [s for s in scenarios if s.process_id == FSL]
    data = json.loads(_file(solution, tests_gen.fixture_path(processes[FSL], fsl[0])).content)
    assert data["request"]["method"] == "GET" and data["request"]["path"] == "/funding/DL-50001"
    assert "body.fundedAt" in data["expected"]["output_noise"]  # seeded relative to now()
    db = _file(solution, "TestDatabase.cs").content
    assert "TEST_DATABASE_URL" in db and "PostgreSqlBuilder" in db and '"gmf"' in db


# ------------------------------------------------------------------- TRX, coverage, metrics
def test_parse_trx_sample():
    cases = trx.parse_trx(GOLDEN / "sample.trx")
    assert len(cases) == 4
    by = {c.display_name: c for c in cases}
    assert sum(c.outcome == "passed" for c in cases) == 3
    failed = [c for c in cases if c.outcome == "failed"]
    assert len(failed) == 1 and "no test body for R2" in failed[0].message
    assert failed[0].rule_id == "R2"
    r1 = by["R1 A dealer must be ACTIVE to receive funding."]
    assert r1.rule_id == "R1" and r1.outcome == "passed" and r1.duration_ms == pytest.approx(12.3456)
    assert r1.name == "GmfCredit.Tests.DealerFundingRequestRulesTests.R1_a_dealer_must_be_active"
    branch = next(c for c in cases if c.branch_id)
    assert branch.branch_id == "LookupDealerToLookupDeal" and branch.rule_id is None
    scenario = next(c for c in cases if c.scenario_id)
    assert scenario.scenario_id == "DealerFundingRequest:happy"


def test_parse_cobertura_sample():
    assert coverage.parse_cobertura(GOLDEN / "sample.cobertura.xml") == pytest.approx(0.625)


def _case(name, outcome, rule=None, branch=None, scenario=None, process=None):
    if process:
        kind = "RulesTests" if rule else "BranchTests" if branch else "ScenarioTests"
        name = f"GmfCredit.Tests.{process}{kind}.{name}"
    return TestCase(name=name, display_name=name, outcome=outcome, message="", rule_id=rule,
                    branch_id=branch, scenario_id=scenario, duration_ms=1.0)


def test_metrics_formulas():
    rules = hand_rules()[DFR]  # R1, R2 approved, R3 edited, R4 draft
    run = TestRun(ok=False, cases=[
        _case("a", "passed", rule="R1"), _case("b", "failed", rule="R2"), _case("c", "passed", rule="R3"),
        _case("d", "passed", branch="T1"), _case("e", "passed", branch="T1"), _case("f", "failed", branch="T2"),
        _case("g", "skipped", scenario="S1"), _case("h", "passed", scenario="S2"),
    ], coverage_line=0.42, trx_path=None, log="")
    build = BuildResult(ok=True, warnings=3, errors=[], log="", files=12)
    m = metrics.compute_metrics(rules, 4, run, build, True, None)
    assert m.rule_carry_forward == pytest.approx(2 / 3)
    assert m.test_pass_rate == pytest.approx(5 / 8)
    assert m.branch_coverage == pytest.approx(1 / 4)
    assert m.code_coverage == pytest.approx(0.42)
    assert m.standards_adherence == pytest.approx(1 - 3 / 12)
    assert m.compile_ok is True and m.warnings == 3 and m.infra_ok is None
    assert set(m.formulas) == {"rule_carry_forward", "test_pass_rate", "branch_coverage", "code_coverage",
                               "standards_adherence", "compile", "infra_validity"}
    assert "approved rules with a passing test" in m.formulas["rule_carry_forward"]
    assert "dotnet format --verify-no-changes" in m.formulas["standards_adherence"]
    # format failure zeroes standards adherence; many warnings floor at 0
    assert metrics.compute_metrics(rules, 4, run, build, False, True).standards_adherence == 0.0
    noisy = BuildResult(ok=True, warnings=40, errors=[], log="", files=12)
    assert metrics.compute_metrics(rules, 4, run, noisy, True, True).standards_adherence == 0.0
    # scoped to one process: R1 of another process does not count
    other = TestRun(ok=True, cases=[_case("x", "passed", rule="R1", process="FundingStatusLookup"),
                                    _case("y", "passed", rule="R2", process="DealerFundingRequest"),
                                    _case("z", "passed", branch="T1", process="FundingStatusLookup")],
                    coverage_line=None, trx_path=None, log="")
    scoped = metrics.compute_metrics(rules, 4, other, build, True, None, process=DFR)
    assert scoped.rule_carry_forward == pytest.approx(1 / 3) and scoped.branch_coverage == 0.0
    empty = metrics.compute_metrics([], 0, TestRun(ok=True, cases=[], coverage_line=None, trx_path=None,
                                                   log=""), build, True, None)
    assert empty.rule_carry_forward is None and empty.test_pass_rate is None and empty.branch_coverage is None
