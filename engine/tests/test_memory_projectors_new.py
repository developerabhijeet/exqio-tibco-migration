"""Projectors of new code, lineage, verify, deploy, gates, events and seeds (pure), plus one
apply against the test Neo4j."""
from datetime import UTC, datetime

import pytest

from engine.dotnet_gen.models import (
    ConfigMap,
    ContractMap,
    DesignRecord,
    MappingRow,
    MessagingEntity,
    ProjectLayout,
    SolutionLayout,
)
from engine.knowledge.memory import load_seed_patterns
from engine.memory.model import NEW_LABELS, MemNode, ScopedDelta, node_hash
from engine.memory.projectors.analysis import ProcessRef
from engine.memory.projectors.build import (
    BuildFileInput,
    BuildInput,
    StepInput,
    TestInput,
    project_build,
)
from engine.memory.projectors.deploy import PlanInput, project_deploy
from engine.memory.projectors.events import EventInput, project_event
from engine.memory.projectors.gate import GateInput, project_gate
from engine.memory.projectors.seed import project_seed
from engine.memory.projectors.verify import DefectInput, ParityInput, RunInput, project_verify
from engine.tests.memory_helpers import apply

ref = ProcessRef(ir_id="proc.One", api_id="api-1", name="One")
IR = ref.ir_id
STARTER_METHOD = f"method:dotnet:{IR}/Api/Flow.Run"
MIG_UID = f"MIGRATED_TO:activity:{IR}/a2->method:dotnet:{IR}/Api/Flow.Query"
NEW_METHOD = f"method:dotnet:{IR}/Api/Flow.Other"


def design() -> DesignRecord:
    rows = [
        MappingRow(activity_id="a1", activity_name="Receive", canonical_type="rest_receive",
                   pattern="controller", project="Api", file="Flow.cs", class_name="Flow",
                   method="Run", rule_ids=["R1"]),
        MappingRow(activity_id="a2", activity_name="Query", canonical_type="jdbc_query",
                   pattern="dapper", project="Core", file="Repo.cs", class_name="Repo",
                   method="Query", rule_ids=["R2"], origin="agent"),
    ]
    return DesignRecord(
        process_id=IR, spec_hash="s",
        solution=SolutionLayout(name="App", projects=[
            ProjectLayout(name="Api", kind="api", files=["Flow.cs"]),
            ProjectLayout(name="Core", kind="core", files=["Repo.cs"])]),
        mapping=rows,
        contracts=[ContractMap(contract_id="c.xsd", record_names=["LoanRequest"],
                               file="Contracts/Loan.cs")],
        config=[ConfigMap(substvar="Timeout", key="App:Timeout"),
                ConfigMap(substvar="DbPass", key="App:DbPass", secret=True)],
        messaging=[MessagingEntity(kind="queue", name="loans", direction="in")])


def build_input(**kw) -> BuildInput:
    base = {
        "build_id": "b1", "build_hash": "h", "design": design(),
        "files": [BuildFileInput(path="src/Api/Flow.cs", origin="code", sha256="x", size=10),
               BuildFileInput(path="src/Core/Repo.cs", origin="agent", sha256="y", size=20),
               BuildFileInput(path="infra/main.tf", origin="infra", sha256="z", size=5)],
        "tests": [TestInput(name="T.Flow.R1", kind="rule", rule_key="R1", outcome="passed")],
        "steps": [StepInput(name="build", ok=True)],
        "starter_activity_id": "a1", "starter_kind": "rest",
        "starter_config": {"verb": "post", "path": "/loans"}, "app_id": "CreditApp"}
    base.update(kw)
    return BuildInput(**base)


def test_every_mapping_row_has_lineage_activity_to_method():
    d = project_build(ref, build_input())
    mig = {(r.src, r.dst) for r in d.rels if r.type == "MIGRATED_TO"}
    assert all((f"activity:{IR}/{m.activity_id}",
                f"method:dotnet:{IR}/{m.project}/{m.class_name}.{m.method}") in mig
               for m in build_input().design.mapping)


def test_new_code_nodes_carry_stack_dotnet():
    nodes = [n for n in project_build(ref, build_input()).nodes if n.label in NEW_LABELS]
    assert nodes and {n.props["stack"] for n in nodes} == {"dotnet"}


def test_test_links_to_method_by_rule_key():
    d = project_build(ref, build_input())
    assert [(r.src, r.dst) for r in d.rels if r.type == "TESTS"] == [
        (f"test:dotnet:{IR}/T.Flow.R1", STARTER_METHOD)]


def test_compile_error_fails_at_the_named_file():
    d = project_build(ref, build_input(
        steps=[StepInput(name="build", ok=False, log="Api/Flow.cs(12,5): error CS1002")]))
    assert ("FAILED_AT", f"codefile:dotnet:{IR}/src/Api/Flow.cs") in {(r.type, r.dst) for r in d.rels}
    assert any(n.uid == f"error:build:{IR}:build" for n in d.nodes)


def test_absolute_compiler_path_resolves_to_the_known_file_and_unknown_goes_to_the_project():
    d = project_build(ref, build_input(steps=[
        StepInput(name="build", ok=False, log="/work/src/Core/Repo.cs(1,1): error"),
        StepInput(name="pack", ok=False, log="/work/Other.cs(1,1): error")]))
    rels = {(r.src, r.type, r.dst) for r in d.rels}
    assert (f"error:build:{IR}:build", "FAILED_AT", f"codefile:dotnet:{IR}/src/Core/Repo.cs") in rels
    assert (f"error:build:{IR}:pack", "ABOUT", f"codeproject:dotnet:{IR}/Api") in rels


def test_failed_test_fails_at_its_method_and_messages_are_cut():
    d = project_build(ref, build_input(tests=[
        TestInput(name="T.Flow.R2", kind="rule", rule_key="R2", outcome="failed",
                  message="x" * 900)]))
    err = next(n for n in d.nodes if n.uid == f"error:test:{IR}:T.Flow.R2")
    assert len(err.text) == len(err.props["message"]) == 500
    assert [(r.type, r.dst) for r in d.rels if r.src == err.uid] == [
        ("FAILED_AT", f"method:dotnet:{IR}/Core/Repo.Query")]


def test_build_tree_lineage_and_paths():
    d = project_build(ref, build_input())
    rels = {(r.type, r.src, r.dst) for r in d.rels}
    sol, api = f"solution:dotnet:{IR}", f"codeproject:dotnet:{IR}/Api"
    assert ("CONTAINS", sol, api) in rels
    assert ("CONTAINS", api, f"codefile:dotnet:{IR}/src/Api/Flow.cs") in rels
    assert ("CONTAINS", f"codefile:dotnet:{IR}/src/Api/Flow.cs", f"class:dotnet:{IR}/Api/Flow") in rels
    assert ("CONTAINS", sol, f"codefile:dotnet:{IR}/infra/main.tf") in rels
    assert ("MIGRATED_TO", f"process:{IR}", api) in rels
    assert ("MIGRATED_TO", f"process:{IR}", f"class:dotnet:{IR}/Api/Flow") in rels
    assert ("MIGRATED_TO", "contract:c.xsd", f"class:dotnet:{IR}/Core/LoanRequest") in rels
    assert ("MIGRATED_TO", "substvar:CreditApp/DbPass", f"configkey:dotnet:{IR}/App:DbPass") in rels
    assert ("MIGRATED_TO", "endpoint:old:POST /loans", "endpoint:new:POST /loans") in rels
    assert ("EXPOSES", STARTER_METHOD, "endpoint:new:POST /loans") in rels
    assert ("USES", api, f"configkey:dotnet:{IR}/App:Timeout") in rels
    assert ("CONSUMES", STARTER_METHOD, f"messaging:dotnet:{IR}/queue/loans") in rels
    secret = next(n for n in d.nodes if n.uid.endswith("App:DbPass"))
    assert secret.props["secret"] is True and set(secret.props) <= {
        "stack", "secret", "artifact_type", "artifact_id", "process_id"}
    assert all(not str(n.props.get("path", "")).startswith("/") for n in d.nodes if n.label != "Endpoint")
    assert project_build(ref, build_input()) == d


def test_compiler_scan_skips_unknown_paths_and_falls_back_to_the_project():
    log = "/usr/lib/Generated.cs(1,1): warn\nApi/Flow.cs(3,4): error CS0103"
    d = project_build(ref, build_input(steps=[StepInput(name="build", ok=False, log=log)]))
    assert ("FAILED_AT", f"codefile:dotnet:{IR}/src/Api/Flow.cs") in {
        (r.type, r.dst) for r in d.rels if r.src == f"error:build:{IR}:build"}
    d = project_build(ref, build_input(steps=[
        StepInput(name="build", ok=False, log="Missing/Nope.cs(1,1): error")]))
    assert [(r.type, r.dst) for r in d.rels if r.src == f"error:build:{IR}:build"] == [
        ("ABOUT", f"codeproject:dotnet:{IR}/Api")]
    assert all(n.props["process_id"] == "api-1" for n in d.nodes if n.label == "Error")


def test_class_and_method_paths_are_the_real_build_paths_and_files_belong_by_segment():
    d = project_build(ref, build_input())
    by = {n.uid: n for n in d.nodes}
    assert by[f"class:dotnet:{IR}/Core/Repo"].props["path"] == "src/Core/Repo.cs"
    assert by[f"method:dotnet:{IR}/Api/Flow.Run"].props["path"] == "src/Api/Flow.cs"
    rels = {(r.type, r.src, r.dst) for r in d.rels}
    assert ("CONTAINS", f"codeproject:dotnet:{IR}/Core",
            f"codefile:dotnet:{IR}/src/Core/Repo.cs") in rels
    rows = [design().mapping[0].model_copy(update={"file": ""})]
    m = project_build(ref, build_input(design=design().model_copy(update={"mapping": rows})))
    assert "path" not in next(n for n in m.nodes if n.label == "Method").props


@pytest.mark.parametrize(("kind", "cfg", "old", "new"), [
    ("rest", {"path": "/loans"}, "endpoint:old:/loans", "endpoint:new:POST /loans"),
    ("rest", {"verb": "get", "path": "/loans"}, "endpoint:old:GET /loans",
     "endpoint:new:GET /loans"),
    ("http", {"resource_path": "/in"}, "endpoint:old:HTTP /in", "endpoint:new:POST /in"),
    ("soap", {"operation": "Quote"}, "endpoint:old:SOAP Quote", "endpoint:new:POST /One"),
])
def test_new_endpoint_and_old_endpoint_uid_follow_the_estate_graph(kind, cfg, old, new):
    d = project_build(ref, build_input(starter_kind=kind, starter_config=cfg))
    rels = {(r.type, r.src, r.dst) for r in d.rels}
    assert ("MIGRATED_TO", old, new) in rels
    assert ("EXPOSES", STARTER_METHOD, new) in rels


def test_substvar_lineage_is_skipped_without_app_id_or_for_resource_fields():
    d = design().model_copy(update={"config": [
        ConfigMap(substvar="Timeout", key="App:Timeout"),
        ConfigMap(substvar="resource:Db.password", key="ConnectionStrings:Db", secret=True)]})
    with_app = project_build(ref, build_input(design=d))
    assert [r.src for r in with_app.rels if r.type == "MIGRATED_TO"
            and r.src.startswith("substvar:")] == ["substvar:CreditApp/Timeout"]
    without = project_build(ref, build_input(design=d, app_id=""))
    assert not any(r.src.startswith("substvar:") for r in without.rels)


def test_curation_without_its_target_or_replacement_is_refused():
    with pytest.raises(ValueError, match="target_uid"):
        project_event(event("note"), None, None)
    with pytest.raises(ValueError, match="replacement_uid"):
        project_event(event("correct_mapping", target_uid=MIG_UID), "Activity", "Query")
    with pytest.raises(ValueError, match="MIGRATED_TO"):
        project_event(event("reject_mapping", target_uid="process:x"), "Activity", "Query")


def test_verify_chunk_and_defect_errors():
    p = ParityInput(build_id="b1", runs=[RunInput(
        run_id="r1", scenario_key="happy", status="failed", parity=87.5,
        defects=[DefectInput(field="amount", expected="1", actual="2", finding_key="F1")])])
    d = project_verify(ref, p, STARTER_METHOD)
    assert d.scope == f"verify:{IR}"
    chunk = next(n for n in d.nodes if n.label == "Chunk")
    assert chunk.uid == f"chunk:{IR}:verify:happy" and chunk.props["kind"] == "finding"
    assert chunk.text == "Scenario happy: failed, parity 87.5%"
    err = f"error:parity:{IR}:happy/amount"
    assert {(r.type, r.dst) for r in d.rels if r.src == err} == {
        ("ABOUT", f"process:{IR}"), ("FAILED_AT", STARTER_METHOD)}
    assert chunk.props["artifact_type"] == "verify_run"


def test_failed_plan_without_a_file_is_about_the_solution():
    d = project_deploy(ref, PlanInput(plan_id="pl1", status="failed", output="Error: quota",
                                      infra_paths=["infra/main.tf"]))
    assert [(r.type, r.dst) for r in d.rels] == [("ABOUT", f"solution:dotnet:{IR}")]


def test_failed_plan_names_the_infra_file_and_a_good_plan_is_empty():
    d = project_deploy(ref, PlanInput(plan_id="pl1", status="failed",
                                      output="infra/main.tf line 3: bad", infra_paths=["infra/main.tf"]))
    assert [(r.type, r.dst) for r in d.rels] == [("FAILED_AT", f"codefile:dotnet:{IR}/infra/main.tf")]
    ok = project_deploy(ref, PlanInput(plan_id="pl2", status="applied"))
    assert ok.scope == f"deploy:{IR}" and not ok.nodes and not ok.rels


def gate_input(**kw) -> GateInput:
    base = {"gate": "G3", "state": "approved", "actor": "sme.vicky", "note": "", "gate_row_id": "g-1",
                "decided_at": datetime(2026, 1, 1, tzinfo=UTC), "design_hash": "dh"}
    base.update(kw)
    return GateInput(**base)


def test_gate_uid_is_stable_across_decisions():
    a = project_gate(ref, gate_input(state="approved"))
    b = project_gate(ref, gate_input(state="rejected", note="no"))
    assert a.nodes[0].uid == b.nodes[0].uid == f"gate:{IR}:G3"
    assert a.scope == f"gate:{IR}:G3" and node_hash(a.nodes[0]) != node_hash(b.nodes[0])
    assert a.nodes[0].text == "G3 approved by sme.vicky"
    assert b.nodes[0].text == "G3 rejected by sme.vicky: no"
    assert a.nodes[0].props["artifact_type"] == "gate"
    assert [(r.type, r.dst) for r in a.rels] == [("ABOUT", f"process:{IR}")]


def event(action, **kw) -> EventInput:
    base = {"id": "mem_abc", "kind": "curation", "action": action, "text": "body",
                "context": {"reason": "because"}, "actor": "sme.vicky",
                "created_at": datetime(2026, 1, 1, tzinfo=UTC)}
    base.update(kw)
    return EventInput(**base)


def test_decision_memory_carries_source_and_about():
    d = project_event(event(None, kind="decision", id="mem_1", text="Use Dapper",
                            context={"about_uid": f"process:{IR}", "kind": "decision"}), None, None)
    n = d.nodes[0]
    assert d.scope == "decision:mem_1" and n.uid == "mem_1" and n.label == "Memory"
    assert {"kind", "source", "created_by", "created_at", "context"} <= set(n.props)
    assert [(r.src, r.dst) for r in d.rels] == [("mem_1", f"process:{IR}")]


def test_correct_mapping_adds_a_corrected_migrated_to_and_a_mapping_memory():
    d = project_event(event("correct_mapping", target_uid=MIG_UID, replacement_uid=NEW_METHOD),
                      "Activity", "Query")
    assert any(r.type == "MIGRATED_TO" and r.props["status"] == "corrected"
               and r.props["curated_by"] == "sme.vicky" and r.src == f"activity:{IR}/a2"
               and r.dst == NEW_METHOD for r in d.rels)
    assert [n.props["kind"] for n in d.nodes if n.label == "Memory"] == ["mapping"]
    assert next(n for n in d.nodes if n.label == "Memory").text == (
        "Map `Query` to `Flow.Other`: because")
    assert ("ABOUT", f"activity:{IR}/a2") in {(r.type, r.dst) for r in d.rels if r.src == "curation:mem_abc"}


def test_reject_note_and_obsolete_shapes():
    rej = project_event(event("reject_mapping", target_uid=MIG_UID), "Activity", "Query")
    assert next(n for n in rej.nodes if n.label == "Memory").text == (
        "Do not map `Query` to `Flow.Query`: because")
    assert not any(r.type == "MIGRATED_TO" for r in rej.rels)
    note = project_event(event("note", target_uid=f"process:{IR}"), "Process", "One")
    assert [n.props["kind"] for n in note.nodes if n.label == "Memory"] == ["note"]
    assert note.nodes[1].props["source"] == "curation"
    obs = project_event(event("obsolete", target_uid="mem_old"), "Memory", "old")
    assert [n.label for n in obs.nodes] == ["Curation"]
    assert [(r.src, r.dst) for r in obs.rels] == [("curation:mem_abc", "mem_old")]


def test_seed_projector_is_idempotent_and_uses_pattern_ids():
    d = project_seed(load_seed_patterns())
    assert d.nodes and all(n.uid.startswith("seed:") and ":" not in n.uid[5:] for n in d.nodes)
    assert all(n.props["source"] == "seed" and n.props["artifact_type"] == "memory"
               for n in d.nodes)
    assert project_seed(load_seed_patterns()) == d


@pytest.mark.usefixtures("hash_embeddings")
async def test_new_side_projections_apply_without_dangling(memory_graph):
    old = ScopedDelta(scope="estate", nodes=[
        MemNode(label="Process", uid=f"process:{IR}", name="One", text="One process"),
        MemNode(label="Activity", uid=f"activity:{IR}/a1", name="Receive", text="Receive"),
        MemNode(label="Activity", uid=f"activity:{IR}/a2", name="Query", text="Query"),
        MemNode(label="Endpoint", uid="endpoint:old:POST /loans", name="POST /loans",
                props={"method": "POST", "path": "/loans", "side": "old"})])
    bi = build_input(steps=[StepInput(name="build", ok=False, log="Api/Flow.cs(1,1): error")],
                     design=design().model_copy(update={"contracts": [], "config": []}))
    deltas = [project_build(ref, bi),
              project_verify(ref, ParityInput(build_id="b1", runs=[RunInput(
                  run_id="r1", scenario_key="s", status="failed", parity=50,
                  defects=[DefectInput(field="f")])]), STARTER_METHOD),
              project_deploy(ref, PlanInput(plan_id="pl", status="failed", output="boom")),
              project_gate(ref, gate_input())]
    await apply(memory_graph, "k0", old)
    res = await apply(memory_graph, "k1", deltas, phase="build")
    assert res.revision is not None and res.dangling == 0 and res.refused == 0
    assert (await apply(memory_graph, "k2", deltas, phase="build")).revision is None
