"""Go tests first, ``GoRunner``, ``GoLanguage`` and the Go Dockerfile (target stacks, Task 13).

The tests that need Go skip with the reason when ``scripts/toolchain.py`` does not find it (like
``test_runner.py::needs_dotnet``); everything else runs without a toolchain. The golden file is
``engine/tests/golden/go/dealer_funding_request_rules_test.go``; regenerate it after an intended
change with ``UPDATE_GOLDEN=1`` and review the diff.
"""
from __future__ import annotations

import json
import os
import re
import socket
import stat
import urllib.request
from pathlib import Path

import pytest

from engine.agents.builder import check_filled_file
from engine.agents.test_engineer import placeholder_methods
from engine.dotnet_gen.design import build_design_skeleton
from engine.infra_gen import generator as infra
from engine.stacks import get_stack
from engine.stacks.base import Mirrors, SourceLanguage
from engine.stacks.generic_checks import NO_ASSERTION, check_generic_test_body
from engine.stacks.go import versions
from engine.stacks.go.generator import generate_go
from engine.stacks.go.language import GoLanguage
from engine.stacks.go.runner import GO_SETTINGS, GoRunner, find_tool, parse_go_errors
from engine.stacks.testindex import INDEX_PATH, IndexEntry, read_index
from engine.tests.conftest import DFR, FSL, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = "gmf-credit"

needs_go = pytest.mark.skipif(not find_tool("go"), reason="go not found (scripts/toolchain.py)")


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
def go_solution_with_tests(tmp_path_factory, gmf_app, go_designs, gmf_rules, gmf_findings, gmf_scenarios):
    return generate_go(gmf_app, PROCESS_IDS, go_designs, gmf_rules, gmf_findings, gmf_scenarios,
                       tmp_path_factory.mktemp("gotests") / "GmfCredit", Mirrors())


@pytest.fixture
def test_database_url():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


# ------------------------------------------------------------------ the brief's tests
def test_go_rules_tests_golden(go_solution_with_tests):
    _golden("go/dealer_funding_request_rules_test.go",
            _file(go_solution_with_tests, "dealer_funding_request_rules_test.go").content)


def test_index_names_every_generated_test(go_solution_with_tests):
    idx = read_index(go_solution_with_tests.root)
    assert {e.kind for e in idx} >= {"rule", "branch", "scenario"}
    assert all(e.name.startswith(f"{MODULE}/tests.Test") for e in idx)


def test_parse_go_build_errors(tmp_path):
    log = ("# gmf-credit/internal/core\n"
           "internal/core/a.go:12:5: undefined: foo\n"
           f"{tmp_path}/internal/core/a.go:12:5: undefined: foo\n"
           "./b.go:3:2: declared and not used: x\n"
           "vet: internal/api/c.go:7:1: unreachable code\n")
    n, errs = parse_go_errors(log, tmp_path)
    assert n == 2 and errs == ["internal/core/a.go:12:5: undefined: foo", "b.go:3:2: declared and not used: x"]


def test_go_language_checks():
    lang = GoLanguage()
    assert lang.placeholder.search('panic("agent fills: X")')
    assert lang.test_placeholder("R1") == 't.Fatal("no test body for R1")'
    assert lang.strip_comments('s := "// no" // yes') == 's := "// no" '
    raw = "q := `select 1 -- \"x\" // not a comment\n`\n/* a\n b */ x := 'r'"
    assert lang.strip_comments(raw) == "q := `select 1 -- \"x\" // not a comment\n`\n    \n      x := 'r'"
    assert lang.code_only(raw).startswith("q := `" + " " * 32 + "\n`")
    assert lang.is_test_path("tests/a_rules_test.go") and lang.is_test_path("internal/core/x_test.go")
    assert not lang.is_test_path("internal/core/x.go")


def test_generic_check_refuses_vacuous_go_body():  # Review Focus 3
    assert check_generic_test_body('if false {\n\tt.Fatal("x")\n}', "go") == NO_ASSERTION
    assert get_stack("go").language.check_test_body('if false {\n\tt.Fatal("x")\n}', "R1") == NO_ASSERTION
    good = ('e := newScenario(t)\nres, err := e.deps.Services.FundingStatusLookup.Execute(ctx, state)\n'
            'if err != nil || res.StatusCode != 200 {\n\tt.Fatalf("got %v", res)\n}')
    assert check_generic_test_body(good, "go") is None


@needs_go
def test_generated_go_builds_vets_formats_and_tests(go_solution_with_tests, test_database_url):
    r = GoRunner(go_solution_with_tests.root, env={"TEST_DATABASE_URL": test_database_url})
    assert r.restore().ok
    build = r.build()
    assert build.ok, build.errors
    assert r.format_check().ok  # the generator writes gofmt layout: no write needed first
    run = r.test()
    assert run.cases and {c.rule_id for c in run.cases if c.rule_id} >= {"R1"} and run.coverage_line is not None
    passed = {c.scenario_id for c in run.cases if c.outcome == "passed"}
    assert {"FundingStatusLookup:happy", "FundingStatusLookup:branch:QueryFundingStatusToReplyNotFound"} <= passed


@needs_go
def test_go_service_answers_health_and_dev_token(go_solution_with_tests):
    r = GoRunner(go_solution_with_tests.root)
    assert r.restore().ok and r.build().ok
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handle = r.run_service(port, env={"ASPNETCORE_ENVIRONMENT": "Development"})
    try:
        with urllib.request.urlopen(handle.url + "/health", timeout=5) as resp:
            assert resp.status == 200
        req = urllib.request.Request(handle.url + "/auth/dev-token", data=b'{"subject":"t"}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            assert body["token_type"] == "Bearer" and body["access_token"]
    finally:
        handle.stop()


# ------------------------------------------------------------------ generated tests and index
def test_rules_tests_have_one_placeholder_per_approved_rule(go_solution_with_tests, gmf_rules):
    text = _file(go_solution_with_tests, "dealer_funding_request_rules_test.go").content
    approved = [r.id for r in gmf_rules[DFR] if r.status in ("approved", "edited")]
    assert approved == ["R1", "R2", "R3"]
    for rid in approved:
        assert f't.Fatal("no test body for {rid}")' in text
    assert "R4" not in text  # a draft rule gets no test
    assert "func TestDealerFundingRequestRules_R1_ADealerMustBeACTIVEToReceiveFunding(t *testing.T) {" in text
    assert "\t_ = http.MethodGet\n" in text  # the vocabulary is referred to, so its imports stay


def test_index_entries_match_the_test_engineer_lookup(go_solution_with_tests):
    """The Test Engineer finds a test by path, process id, kind and the last segment of its name."""
    files = {f.path: f.content for f in go_solution_with_tests.files}
    idx = read_index(go_solution_with_tests.root)
    for e in idx:
        assert e.path in files and e.process_id in (DFR, FSL)
        name = e.name.rsplit(".", 1)[-1]
        assert e.name == f"{MODULE}/tests.{name}" and f"func {name}(t *testing.T) {{" in files[e.path]
    rules = [e for e in idx if e.kind == "rule" and e.process_id == DFR]
    assert [e.rule_id for e in rules] == ["R1", "R2", "R3"] and not any(e.has_body for e in rules)
    found = placeholder_methods(files[rules[0].path], "go", rules)
    assert [m.ref for m in found] == ["R1", "R2", "R3"]
    lines = files[rules[0].path].split("\n")
    assert lines[found[0].body_start].strip() == 't.Fatal("no test body for R1")'
    branches = [e for e in idx if e.kind == "branch" and e.process_id == FSL]
    assert branches and all(e.has_body for e in branches)  # every FSL branch has a driving scenario
    scenarios = {e.scenario_id for e in idx if e.kind == "scenario"}
    assert {"FundingStatusLookup:happy", "DealerFundingRequest:happy"} <= scenarios


def test_scenario_drivers_follow_the_starter(go_solution_with_tests):
    fsl = _file(go_solution_with_tests, "funding_status_lookup_scenario_test.go").content
    assert 'newScenario(t).runRequest("FundingStatusLookup", scenario)' in fsl
    dfr = _file(go_solution_with_tests, "dealer_funding_request_scenario_test.go").content
    assert ('e.runMessage("DealerFundingRequest", scenario, messaging.DealerFundingRequestEntity, '
            'map[string]string{"MsgType": "FUNDING_REQUEST"}, '
            'messaging.NewDealerFundingRequestConsumer(e.deps.Services.DealerFundingRequest).Handle)') in dfr
    branch = _file(go_solution_with_tests, "dealer_funding_request_branch_test.go").content
    assert 't.Fatal("no test body for branch LookupDealerToThrowDealerInactive: no scenario takes this link")' in branch
    assert 'runDealerFundingRequestScenario(t, "happy")' in branch
    support = _file(go_solution_with_tests, "tests/scenario_support_test.go").content
    assert "httptest.NewServer(api.NewRouter(deps))" in support


def test_fixtures_are_the_dotnet_documents(go_solution_with_tests):
    files = {f.path: f.content for f in go_solution_with_tests.files}
    happy = json.loads(files["tests/fixtures/FundingStatusLookup/happy.json"])
    assert happy["request"]["path"] == "/funding/DL-50001" and happy["expected"]["output"]["status"] == 200
    assert "body.fundedAt" in happy["expected"]["output_noise"]
    assert "tests/database/schema.sql" in files and "tests/database/seed.sql" in files


def test_test_support_reads_the_test_database_and_skips_without_it(go_solution_with_tests):
    db = _file(go_solution_with_tests, "tests/testdb_test.go").content
    assert 'os.Getenv("TEST_DATABASE_URL")' in db and 't.Skip("TEST_DATABASE_URL is not set")' in db
    assert 'var connectionKeys = []string{"CONNECTION_STRINGS_GMF_FUNDING_DB"}' in db
    assert 'var testSchemas = []string{"gmf"}' in db and 'strings.HasSuffix(name, "_test")' in db


def test_the_stack_names_the_fixture_the_test_engineer_shows(go_solution_with_tests, gmf_processes, gmf_scenarios):
    from engine.agents.test_engineer import _scenarios_block

    stack = get_stack("go")
    paths = {f.path for f in go_solution_with_tests.files}
    scenarios = [s for s in gmf_scenarios if s.process_id == FSL]
    assert scenarios and all(stack.fixture_path(gmf_processes[FSL], s) in paths for s in scenarios)
    block = _scenarios_block(go_solution_with_tests, gmf_processes[FSL], scenarios, "go")
    assert "tests/fixtures/FundingStatusLookup/" in block and '"scenario_id"' in block


# ------------------------------------------------------------------ GoLanguage
def test_go_language_is_the_stack_language():
    lang = get_stack("go").language
    assert isinstance(lang, GoLanguage) and isinstance(lang, SourceLanguage)


def test_declarations_are_func_signatures_and_type_lines():
    text = '''package core

// S is a service.
type S struct {
	repo *Repo
}

type Value = any

func (s *S) Execute(ctx context.Context, state *tib.State) (tib.Result, error) {
	if x := f(); x {
		return tib.Result{}, nil
	}
	return state.Finish(), nil
}

func helper(
	a int,
	b string,
) error {
	return nil
}

func TestX_R1(t *testing.T) {
	t.Fatal("no test body for R1")
}
'''
    assert GoLanguage().declarations(text) == [
        "type S struct", "type Value = any",
        "func (s *S) Execute(ctx context.Context, state *tib.State) (tib.Result, error)",
        "func helper(a int, b string) error", "func TestX_R1(t *testing.T)"]
    assert GoLanguage().tibco_comments("\t// TIBCO: x = 1\n\t// note\ns := \"// TIBCO: no\"") == ["// TIBCO: x = 1"]


MAIN = "internal/core/loan_mappings.go"
SCAFFOLD = '''package core

import "gmf-credit/internal/tib"

func (s *LoanMappings) Map(state *tib.State) tib.Value {
	// TIBCO: total = $Start/a + $Start/b
	panic("agent fills: map")
}
'''


def _fill(body: str) -> str:
    return SCAFFOLD.replace('\tpanic("agent fills: map")\n', body)


@pytest.mark.parametrize(("body", "reason"), [
    ('\tt.Fatal("x")\n\treturn nil\n', "assertion"),
    ("\t_ = testing.Short\n\treturn nil\n", "assertion"),
    ("\treturn nil\n}\n\nfunc TestExtra(t *T) {\n", "test function"),
    ("\t//nolint:errcheck\n\treturn nil\n", "nolint"),
    ("\tdefer func() { recover() }()\n\treturn nil\n", "swallow a TIBCO fault"),
    ('\tif tib.FaultNamed(nil, "X") {\n\t\treturn nil\n\t}\n\treturn nil\n', "swallow a TIBCO fault"),
    ('\treturn tib.Of("/etc/passwd")\n', "outside the project"),
])
def test_go_fill_may_not_introduce(body, reason):
    assert reason in check_filled_file(MAIN, SCAFFOLD, _fill(body), stack="go")


def test_go_fill_may_mention_them_in_comments_and_strings():
    body = '\t// no t.Fatal( here and no recover() either\n\tnote := "t.Errorf is a test thing"\n\treturn tib.Of(note)\n'
    assert check_filled_file(MAIN, SCAFFOLD, _fill(body), stack="go") is None


# ------------------------------------------------------------------ GoRunner without Go
def _fake_tool(tmp_path: Path, name: str, code: int = 0, out: str = "") -> Path:
    log = tmp_path / "calls.txt"
    exe = tmp_path / name
    exe.write_text(f"#!/bin/sh\necho \"{name} $@\" >> {log}\nenv >> {log}.env\nprintf %b '{out}'\nexit {code}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_runner_commands_are_the_spec_s(tmp_path):
    go, gofmt = _fake_tool(tmp_path, "go"), _fake_tool(tmp_path, "gofmt")
    r = GoRunner(tmp_path, go=str(go), gofmt=str(gofmt))
    assert r.restore().ok and r.build().ok and r.format_write().ok and r.format_check().ok
    r.test()
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    assert calls == ["go mod download", "go build -o .exiqo-bin/server ./cmd/server", "go vet ./...", "gofmt -w .",
                     "gofmt -l .", "go test -json -coverpkg=./internal/... -coverprofile=.exiqo-bin/cover.out ./..."]


def test_format_check_fails_naming_the_unformatted_files(tmp_path):
    gofmt = _fake_tool(tmp_path, "gofmt", 0, "internal/core/a.go\\ntests/b_test.go\\n")
    step = GoRunner(tmp_path, go="go", gofmt=str(gofmt)).format_check()
    assert not step.ok and "internal/core/a.go" in step.log and "tests/b_test.go" in step.log


def test_runner_environment_pins_go_and_drops_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GOPROXY", "https://goproxy.gmf.internal/go")
    monkeypatch.setenv("GOMODCACHE", "/cache/stacks/gomod")
    monkeypatch.setenv("GOFLAGS", "-mod=mod")  # the platform cannot loosen the readonly rule
    monkeypatch.setenv("APP_SECRET", "s3cret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    env = GoRunner(tmp_path, go="/usr/local/go/bin/go", env={"TEST_DATABASE_URL": "postgresql://t"}).env({"X": "1"})
    assert {k: env[k] for k in GO_SETTINGS} == GO_SETTINGS and env["GOFLAGS"] == "-mod=readonly"
    assert env["GOPROXY"] == "https://goproxy.gmf.internal/go" and env["GOMODCACHE"] == "/cache/stacks/gomod"
    assert "APP_SECRET" not in env and "DATABASE_URL" not in env
    assert env["TEST_DATABASE_URL"] == "postgresql://t" and env["X"] == "1"
    assert env["PATH"].split(os.pathsep)[0] == "/usr/local/go/bin"


def test_build_errors_come_from_the_compiler_or_the_log(tmp_path):
    log = "# gmf-credit/internal/core\\ninternal/core/a.go:3:9: undefined: x\\n"
    b = GoRunner(tmp_path, go=str(_fake_tool(tmp_path, "go", 1, log))).build()
    assert not b.ok and b.errors == ["internal/core/a.go:3:9: undefined: x"]
    b2 = GoRunner(tmp_path, go=str(_fake_tool(tmp_path, "go2", 1, "go: missing go.sum entry for module x\\n"))).build()
    assert b2.errors == ["go: missing go.sum entry for module x"]


def test_test_run_joins_test2json_to_the_index(tmp_path):
    (tmp_path / ".exiqo").mkdir()
    (tmp_path / INDEX_PATH).write_text(json.dumps([IndexEntry(
        name="gmf-credit/tests.TestA_R1_X", kind="rule", rule_id="R1", path="p").model_dump()]))
    stream = ('{"Action":"output","Package":"gmf-credit/tests","Test":"TestA_R1_X","Output":"no test body for R1\\\\n"}\\n'
              '{"Action":"fail","Package":"gmf-credit/tests","Test":"TestA_R1_X","Elapsed":0.01}\\n')
    run = GoRunner(tmp_path, go=str(_fake_tool(tmp_path, "go", 1, stream))).test()
    assert not run.ok and [(c.rule_id, c.outcome) for c in run.cases] == [("R1", "failed")]
    assert "no test body for R1" in run.cases[0].message and run.coverage_line is None


# ------------------------------------------------------------------ Dockerfile, pipelines, image
@pytest.fixture(scope="module")
def go_infra(go_solution_with_tests, go_designs, gmf_app):
    files = infra.generate_infra(gmf_app, list(go_designs.values()), go_solution_with_tests.root,
                                 go_solution_with_tests.name, stack="go")
    return {f.path: f.content for f in files}


def test_go_dockerfile_builds_the_binary_offline_as_nonroot(go_infra, go_solution_with_tests):
    df = go_infra["Dockerfile"]
    assert re.search(r"^ARG BASE_IMAGE_BUILD$", df, re.MULTILINE) and re.search(r"^ARG BASE_IMAGE_RUNTIME$", df, re.MULTILINE)
    assert "ARG GOPROXY=" in df and "CGO_ENABLED=0" in df and re.search(r"^USER 65532$", df, re.MULTILINE)
    for setting in ("GOFLAGS=-mod=readonly", "GOSUMDB=off", "GOTOOLCHAIN=local", "GOTELEMETRY=off"):
        assert setting in df
    assert f"golang:{versions.GO_VERSION}" in df and "gcr.io/distroless/static-debian12:nonroot" in df  # documented
    assert 'ENTRYPOINT ["/app/server"]' in df and "go build -trimpath" in df and "./cmd/server" in df
    for line in df.splitlines():  # every COPY source exists in the generated project
        if line.startswith("COPY ") and "--from" not in line:
            for src in line.split()[1:-1]:
                assert (go_solution_with_tests.root / src).exists(), line
    assert "**/.exiqo-bin" in go_infra[".dockerignore"]


def test_go_pipelines_build_and_test_with_go(go_infra):
    import yaml

    gh = go_infra[".github/workflows/gmf-credit.yml"]
    ado = go_infra["azure-pipelines.yml"]
    for text in (gh, ado):
        assert "go build -o .exiqo-bin/server ./cmd/server && go vet ./..." in text and "go mod download" in text
        assert "GOPROXY" in text and "GOSUMDB" in text and "BASE_IMAGE_BUILD" in text
        assert "setup-java" not in text and "JavaToolInstaller" not in text and "dotnet" not in text.lower()
    assert "actions/setup-go@v5" in gh and "GoTool@0" in ado and f'"{versions.GO_VERSION}"' in gh
    assert yaml.safe_load(gh)["jobs"] and yaml.safe_load(ado)["stages"]


def test_image_pins_the_go_toolchain_of_the_stack():
    df = (REPO_ROOT / "deploy" / "docker" / "platform.Dockerfile").read_text(encoding="utf-8")
    assert f"ARG GO_VERSION={versions.GO_VERSION}\n" in df
    assert re.search(r"^ARG GO_SHA256_AMD64=[0-9a-f]{64}$", df, re.MULTILINE)
    assert re.search(r"^ARG GO_SHA256_ARM64=[0-9a-f]{64}$", df, re.MULTILINE) and "ARG GO_BASE_URL=" in df
    assert "/usr/local/go/bin" in df and "sha256sum -c -" in df
    for env in ("GOMODCACHE=/cache/stacks/gomod", "GOCACHE=/cache/stacks/gobuild", "GOTOOLCHAIN=local",
                "GOTELEMETRY=off", "GOFLAGS=-mod=readonly", "GOSUMDB=off"):
        assert env in df, env
    images = (REPO_ROOT / "deploy" / "tests" / "test_images.sh").read_text(encoding="utf-8")
    assert "go version" in images and "GOTOOLCHAIN" in images


# ------------------------------------------------------------------ review fix round 1
_LENIENT_CHECK = '''package tib_test

import (
	"testing"

	"gmf-credit/internal/core/contracts/dealerfunding"
	"gmf-credit/internal/tib"
)

type loan struct {
	Count  tib.Int    `json:"count"`
	Big    tib.Int64  `json:"big"`
	Rate   tib.Float  `json:"rate"`
	Active tib.Boolean `json:"active"`
	Name   tib.String `json:"name"`
}

func TestInvalidIsAsLenientAsJackson(t *testing.T) {
	ok := []string{
		`{"count": 5, "big": 7, "rate": 1.5, "active": true, "name": "a"}`,
		`{"count": "5", "big": "7", "rate": "1.5", "active": "true", "name": 123}`,
		`{"count": 12.0, "big": 1e2, "name": false}`,
		`{"count": null, "name": null}`,
	}
	for _, body := range ok {
		v, _ := tib.Parse(body)
		if got := tib.Invalid(v, &loan{}); got != "" {
			t.Errorf("%s: refused: %s", body, got)
		}
	}
	bad := []string{`{"count": "x"}`, `{"count": 12.5}`, `{"active": "yes"}`, `{"big": [1]}`, `{"name": {"a": 1}}`}
	for _, body := range bad {
		v, _ := tib.Parse(body)
		if tib.Invalid(v, &loan{}) == "" {
			t.Errorf("%s: accepted", body)
		}
	}
	v, _ := tib.Parse(`{"dealId": 123, "dealerId": "D1", "fundingAmount": "31500.00", "contractDate": "2026-01-01"}`)
	if got := tib.Invalid(v, &dealerfunding.FundingRequest{}); got != "" {
		t.Errorf("the generated contract refuses a number for a string: %s", got)
	}
}
'''

_DSN_CHECK = '''package infrastructure_test

import (
	"testing"

	"gmf-credit/internal/infrastructure"
)

func TestDSNKeepsTheSSLMode(t *testing.T) {
	cases := map[string]string{
		"Host=h;Port=5432;Database=d;Username=u;Password=p":                         "postgres://u:p@h:5432/d",
		"Host=h;Database=d;Username=u;SSL Mode=Require":                             "postgres://u@h:5432/d?sslmode=verify-full",
		"Host=h;Database=d;Username=u;SslMode=VerifyFull":                           "postgres://u@h:5432/d?sslmode=verify-full",
		"Host=h;Database=d;Username=u;SSL Mode=Require;Trust Server Certificate=true": "postgres://u@h:5432/d?sslmode=require",
		"Host=h;Database=d;Username=u;Ssl Mode=VerifyCA":                            "postgres://u@h:5432/d?sslmode=verify-ca",
		"Host=h;Database=d;Username=u;SSL Mode=Disable":                             "postgres://u@h:5432/d?sslmode=disable",
		"Host=x.postgres.database.azure.com;Database=d;Username=u":                  "postgres://u@x.postgres.database.azure.com:5432/d?sslmode=verify-full",
	}
	for in, want := range cases {
		if got := infrastructure.DSN(in); got != want {
			t.Errorf("%s: got %s, want %s", in, got, want)
		}
	}
}
'''


@needs_go
def test_contract_validation_and_dsn_in_go(go_solution_with_tests, tmp_path):
    """I1 and M4 in the generated runtime: tib.Invalid coerces scalars like Jackson, DSN keeps the
    Npgsql SSL mode (verify-full for Require and VerifyFull, and for Azure Database hosts)."""
    import shutil

    root = tmp_path / "copy"
    shutil.copytree(go_solution_with_tests.root, root, ignore=shutil.ignore_patterns(".exiqo-bin"))
    (root / "internal/tib/lenient_check_test.go").write_text(_LENIENT_CHECK, encoding="utf-8")
    (root / "internal/infrastructure/dsn_check_test.go").write_text(_DSN_CHECK, encoding="utf-8")
    r = GoRunner(root)
    assert r.restore().ok
    code, log = r._run([r.go, "test", "-count=1", "./internal/tib/", "./internal/infrastructure/"])
    assert code == 0, log


def test_runner_proxy_fails_closed(tmp_path, monkeypatch):
    for name in ("GOPROXY", "GOPRIVATE", "GONOPROXY", "GONOSUMDB"):
        monkeypatch.delenv(name, raising=False)
    r = GoRunner(tmp_path, go="/usr/local/go/bin/go")
    assert r.env()["GOPROXY"] == "off"  # no mirror configured: nothing is fetched
    monkeypatch.setenv("GOPROXY", "")
    assert GoRunner(tmp_path, go="go").env()["GOPROXY"] == "off"
    monkeypatch.setenv("GOPRIVATE", "github.com/gmf/*")
    monkeypatch.setenv("GONOPROXY", "*")
    monkeypatch.setenv("GONOSUMDB", "*")
    env = GoRunner(tmp_path, go="go").env()
    assert not {"GOPRIVATE", "GONOPROXY", "GONOSUMDB"} & set(env)  # nothing routes around the mirror


@pytest.mark.parametrize("proxy", ["https://goproxy.gmf.internal/go,direct", "direct",
                                   "https://proxy.golang.org", "https://goproxy.gmf.internal/go|direct"])
def test_runner_refuses_a_proxy_that_reaches_public_hosts(tmp_path, monkeypatch, proxy):
    monkeypatch.setenv("GOPROXY", proxy)
    go = _fake_tool(tmp_path, "go")
    step = GoRunner(tmp_path, go=str(go)).restore()
    assert not step.ok and "GOPROXY" in step.log
    assert not (tmp_path / "calls.txt").exists()  # go never ran


def test_runner_takes_the_configured_mirror_and_an_explicit_public_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("GOPROXY", "https://goproxy.gmf.internal/go")
    go = _fake_tool(tmp_path, "go")
    assert GoRunner(tmp_path, go=str(go)).restore().ok
    # a caller that names the public proxy on purpose (a local check) may, never with direct
    explicit = GoRunner(tmp_path, go=str(go), env={"GOPROXY": "https://proxy.golang.org"})
    assert explicit.restore().ok
    assert not GoRunner(tmp_path, go=str(go), env={"GOPROXY": "https://proxy.golang.org,direct"}).restore().ok


def test_pipelines_and_dockerfile_refuse_an_empty_or_direct_proxy(go_infra):
    for name in (".github/workflows/gmf-credit.yml", "azure-pipelines.yml"):
        text = go_infra[name]
        assert 'test -n "$GOPROXY"' in text and "*direct*" in text, name
    assert "*direct*" in go_infra["Dockerfile"]


def test_runner_trusts_the_mirror_ca_bundle(tmp_path, monkeypatch):
    """A module mirror behind GMF's private CA: Go reads SSL_CERT_FILE (as the Python runner does), and
    the file is the system roots plus the mirror CA, so a public mirror still verifies."""
    from engine.stacks import cabundle

    system, pem = tmp_path / "system.pem", tmp_path / "ca.pem"
    system.write_text("-----BEGIN CERTIFICATE-----\nSYSTEM\n-----END CERTIFICATE-----\n")
    pem.write_text("-----BEGIN CERTIFICATE-----\nMIRROR\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(cabundle, "system_bundle", lambda: system)
    monkeypatch.setattr(cabundle, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("MIRROR_CA_BUNDLE", str(pem))
    text = Path(GoRunner(tmp_path, go="go").env()["SSL_CERT_FILE"]).read_text()
    assert "SYSTEM" in text and "MIRROR" in text
    monkeypatch.setenv("MIRROR_CA_BUNDLE", str(tmp_path / "absent.pem"))
    assert "SSL_CERT_FILE" not in GoRunner(tmp_path, go="go").env()
