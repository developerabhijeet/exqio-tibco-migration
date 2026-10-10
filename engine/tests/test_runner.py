"""DotnetRunner against generated solutions (Task 14).

Tests that need ``dotnet`` skip with the reason printed when it is absent. Tests that need
third-party NuGet packages skip when ``api.nuget.org`` is unreachable and no offline cache exists:
run ``make nuget-cache`` on a machine with NuGet access and copy ``.tools/nuget-cache`` here.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from engine.analysis.models import Citation, Rule
from engine.analysis.static_rules import run_static_rules
from engine.domains import graph as graph_mod
from engine.dotnet_gen import generator, runner
from engine.dotnet_gen.design import build_design_skeleton
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
FSL = "dealerfunding.FundingStatusLookup"
CAI = "creditorigination.CreditApplicationIntake"
DFR = "dealerfunding.DealerFundingRequest"
NUGET_SKIP = "NuGet unreachable: run `make nuget-cache` on a machine with NuGet access"

DOTNET = runner.find_dotnet()
needs_dotnet = pytest.mark.skipif(DOTNET is None, reason="dotnet SDK not found (scripts/toolchain.py)")
if DOTNET is None:  # make the reason visible in the plain test output too
    print("test_runner: dotnet SDK not found; dotnet tests will be skipped")


@pytest.fixture(scope="module")
def apps():
    return {a.id: a for a in scan_folder(GMF).applications}


def _generate(apps, process_ids, out: Path, rules=None):
    app = apps["gmf.credit"]
    graph = graph_mod.build_graph(list(apps.values()))
    procs = {p.id: p for p in app.processes}
    rules = rules or {}
    designs, findings = {}, {}
    for pid in process_ids:
        p = procs[pid]
        findings[pid] = [f.model_copy(update={"decision": "keep"})
                         for f in run_static_rules(p, app.contracts, app.substvars)]
        designs[pid] = build_design_skeleton(p, app, rules.get(pid, []), findings[pid],
                                             graph_mod.io_card(p, app, graph))
    return generator.generate_solution(app, process_ids, designs, rules, findings, [], out)


# -------------------------------------------------------------- no dotnet needed
def test_parse_build_log():
    warn = "/s/src/A/Foo.cs(3,5): warning CS8618: Non-nullable property 'X' must contain a non-null value"
    err = "/s/src/A/Foo.cs(9,1): error CS0103: The name 'y' does not exist in the current context"
    proj = " [/s/src/A/A.csproj]"
    log = (f"  Determining projects to restore...\n{warn}{proj}\n{err}{proj}\n{warn}{proj}\n{err}{proj}\n"
           "Build FAILED.\n    1 Warning(s)\n    1 Error(s)\n")
    warnings, errors = runner.parse_build_log(log)
    assert warnings == 1
    assert errors == ["/s/src/A/Foo.cs(9,1): error CS0103: The name 'y' does not exist in the current context"]


def test_nuget_unreachable_detection():
    assert runner.nuget_unreachable("error NU1301: Unable to load the service index for source https://api.nuget.org/v3/index.json.")
    assert not runner.nuget_unreachable("error CS0103: The name 'y' does not exist")


def test_runner_environment_points_at_local_toolchain(tmp_path):
    r = runner.DotnetRunner(tmp_path, dotnet="/opt/x/dotnet")
    env = r.env()
    assert env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1" and env["DOTNET_NOLOGO"] == "1"
    assert env["DOTNET_ROOT"] == "/opt/x"


SECRETS = {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "AZURE_OPENAI_API_KEY": "key", "APP_SECRET": "s3cret",
           "LANGFUSE_SECRET_KEY": "lf", "DATABASE_URL": "postgresql://u:p@db/x"}


def _dump_env_tool(tmp_path: Path, name: str) -> tuple[str, Path]:
    """A stand-in binary that writes its environment to a file and exits 0."""
    dump = tmp_path / f"{name}.env"
    tool = tmp_path / "bin" / name
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.write_text(f'#!/bin/sh\n/usr/bin/env > "{dump}"\nexit 0\n')
    tool.chmod(0o755)
    return str(tool), dump


def _read_env(dump: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)


def test_runner_environment_drops_api_secrets(tmp_path, monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("NUGET_PACKAGES", "/n")
    r = runner.DotnetRunner(tmp_path, dotnet="/opt/x/dotnet")
    env = r.env({"TEST_DATABASE_URL": "postgresql://t/x"})
    assert not set(SECRETS) & set(env)
    assert env["TEST_DATABASE_URL"] == "postgresql://t/x"
    assert env["HTTPS_PROXY"] == "http://proxy:3128" and env["LC_ALL"] == "C.UTF-8"
    assert env["NUGET_PACKAGES"] == "/n" and env["PATH"].startswith("/opt/x")


def test_runner_child_process_gets_scrubbed_environment(tmp_path, monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    dotnet, dump = _dump_env_tool(tmp_path, "dotnet")
    r = runner.DotnetRunner(tmp_path, dotnet=dotnet)
    r.test(project="x.csproj", env={"TEST_DATABASE_URL": "postgresql://t/x"})
    child = _read_env(dump)
    assert not set(SECRETS) & set(child)
    assert child["TEST_DATABASE_URL"] == "postgresql://t/x"
    # the runner-wide env applies to every step (the repair loop calls build and test without arguments)
    r = runner.DotnetRunner(tmp_path, dotnet=dotnet, env={"TEST_DATABASE_URL": "postgresql://t/y"})
    r.build(project="x.csproj")
    child = _read_env(dump)
    assert child["TEST_DATABASE_URL"] == "postgresql://t/y" and not set(SECRETS) & set(child)


# ----------------------------------------------------------------- dotnet needed
@needs_dotnet
def test_runner_resolves_dotnet_from_toolchain(tmp_path):
    r = runner.DotnetRunner(tmp_path)
    assert r.dotnet == DOTNET and Path(r.dotnet).exists()


@needs_dotnet
def test_core_project_builds_without_nuget(tmp_path, apps):
    sol = _generate(apps, [FSL, DFR], tmp_path / "GmfCredit")
    r = runner.DotnetRunner(sol.root)
    result = r.build(project="src/GmfCredit.Core/GmfCredit.Core.csproj")
    assert result.ok, result.log[-4000:]
    assert result.errors == []
    assert result.files > 10


@needs_dotnet
def test_generated_code_is_formatted(tmp_path, apps):
    sol = _generate(apps, [FSL, DFR], tmp_path / "GmfCredit")
    result = runner.DotnetRunner(sol.root).format_check(whitespace_only=True)
    assert result.ok, result.log[-4000:]


@needs_dotnet
def test_runner_builds_scaffold(tmp_path, apps):
    rules = {FSL: [Rule(id="R5", text="Funding status is looked up by deal id.", confidence=0.9,
                        status="approved",
                        citations=[Citation(activity_id="QueryFundingStatus", kind="sql",
                                            text="where f.deal_id = ?")])]}
    sol = _generate(apps, [FSL], tmp_path / "GmfCredit", rules)
    r = runner.DotnetRunner(sol.root)
    if not runner.nuget_available():
        pytest.skip(NUGET_SKIP)
    restore = r.restore()
    if not restore.ok and runner.nuget_unreachable(restore.log):
        pytest.skip(NUGET_SKIP)
    assert restore.ok, restore.log[-4000:]
    build = r.build()
    assert build.ok, build.errors
    run = r.test()
    rule_cases = [c for c in run.cases if c.rule_id == "R5"]
    assert rule_cases and all(c.outcome == "failed" for c in rule_cases)
    assert "no test body" in rule_cases[0].message
    assert run.trx_path is not None and Path(run.trx_path).exists()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@needs_dotnet
def test_run_service_waits_for_health_and_stops(tmp_path):
    # A web project with no NuGet packages builds offline, so run_service is exercised here.
    proj = tmp_path / "svc" / "src" / "Health.Api"
    proj.mkdir(parents=True)
    (proj / "Health.Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web">\n  <PropertyGroup>\n'
        "    <TargetFramework>net10.0</TargetFramework>\n    <ImplicitUsings>enable</ImplicitUsings>\n"
        "  </PropertyGroup>\n</Project>\n")
    (proj / "Program.cs").write_text(
        "var app = WebApplication.CreateBuilder(args).Build();\n"
        'app.MapGet("/health", () => "Healthy");\napp.Run();\n')
    r = runner.DotnetRunner(tmp_path / "svc")
    port = _free_port()
    handle = r.run_service("Health.Api", port, {"ASPNETCORE_ENVIRONMENT": "Development"}, timeout_s=240)
    try:
        assert handle.url == f"http://127.0.0.1:{port}"
        import urllib.request
        with urllib.request.urlopen(handle.url + "/health", timeout=5) as resp:
            assert resp.status == 200
    finally:
        handle.stop()
    assert handle.poll() is not None
    assert Path(handle.log_path).exists()


PROBE = """
using GmfCredit.Core.Contracts;
using GmfCredit.Core.Data;
using GmfCredit.Core.Tibco;

Console.WriteLine("record=" + new CheckDuplicateApplicationInput { Ssn = "909-32-1184" });
Console.WriteLine("text=" + Masking.MaskText("SSN 909-32-1184 account 4000123412341234 ok"));
Console.WriteLine("json=" + Masking.MaskJson(new { accountNumber = "GMF-10000006", amount = 12.5m })!.ToJsonString());

// diamond: A -> B (taken), A -> C (not taken), B -> D, C -> D: D runs once, after B
var flow = new ProcessFlow([new("ab", "A", "B"), new("ac", "A", "C"), new("bd", "B", "D"), new("cd", "C", "D")]);
var order = new List<string>();
flow.Start("A");
while (flow.TryNext(out var step))
{
    order.Add(step);
    flow.Complete(step, step switch { "A" => ["ab"], "B" => ["bd"], "C" => ["cd"], _ => [] });
}
Console.WriteLine("diamond=" + string.Join(",", order));

// parallel join: A -> B and A -> C (both taken), B -> D, C -> D: D waits for both
flow = new ProcessFlow([new("ab", "A", "B"), new("ac", "A", "C"), new("bd", "B", "D"), new("cd", "C", "D")]);
order.Clear();
flow.Start("A");
while (flow.TryNext(out var step))
{
    order.Add(step);
    flow.Complete(step, step switch { "A" => ["ab", "ac"], "B" => ["bd"], "C" => ["cd"], _ => [] });
}
Console.WriteLine("join=" + string.Join(",", order));
Console.WriteLine("tib=" + TibValue.Of("12.50").AsDecimal() + "|" + (TibValue.Of("x").AsInt() is null) + "|" + TibValue.Of(31500.00m).AsString());
"""


@needs_dotnet
def test_core_runtime_masking_flow_and_values(tmp_path, apps):
    """Runs generated Core code: masked ToString, ProcessFlow join semantics, explicit TibValue conversions."""
    sol = _generate(apps, [CAI], tmp_path / "GmfCredit")
    probe = tmp_path / "probe"
    probe.mkdir()
    core = sol.root / "src" / "GmfCredit.Core" / "GmfCredit.Core.csproj"
    (probe / "Probe.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n    <OutputType>Exe</OutputType>\n'
        "    <TargetFramework>net10.0</TargetFramework>\n    <ImplicitUsings>enable</ImplicitUsings>\n"
        "    <Nullable>enable</Nullable>\n  </PropertyGroup>\n  <ItemGroup>\n"
        f'    <ProjectReference Include="{core}" />\n  </ItemGroup>\n</Project>\n')
    (probe / "Program.cs").write_text(PROBE)
    r = runner.DotnetRunner(probe)
    code, log = r._run(["run", "--project", str(probe / "Probe.csproj")], timeout_s=600)
    assert code == 0, log[-4000:]
    out = dict(line.split("=", 1) for line in log.splitlines() if "=" in line and line.split("=", 1)[0].isalpha())
    assert out["record"] == "CheckDuplicateApplicationInput { Ssn = ***-**-1184 }"
    assert out["text"] == "SSN ***-**-1184 account ************1234 ok"
    assert '"accountNumber":"***-****0006"' in out["json"] and '"amount":12.5' in out["json"]
    assert out["diamond"] == "A,B,D"
    assert out["join"] == "A,B,C,D"
    assert out["tib"] == "12.50|True|31500.00"
