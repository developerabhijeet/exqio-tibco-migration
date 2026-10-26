"""Memory across iterations (sub-project 6, Task 5): the iteration projector, its revision and the
iteration filter and preference of the reads."""
from __future__ import annotations

import pytest

from engine.memory.model import MemNode, MemoryHit, ScopedDelta
from engine.memory.projectors.build import BuildFileInput
from engine.memory.projectors.iteration import (
    EditedFile,
    IterationIngest,
    IterationInputRef,
    IterationProcess,
    project_iteration,
)
from engine.memory.search import hybrid, prefer
from engine.tests.memory_helpers import P, meta

pytestmark = pytest.mark.usefixtures("hash_embeddings")

IR = "dealerfunding.DealerFundingRequest"
REQUIREMENT_UID = f"chunk:{IR}:requirement:2"


def sample_ingest(n: int = 2, edit_set: list[EditedFile] | None = None) -> IterationIngest:
    return IterationIngest(project_id=P, n=n, iteration_id=f"it{n}", edit_set=edit_set, processes=[
        IterationProcess(ir_id=IR, api_id="api-dfr",
                         requirements="Funding over 50k needs a second approver before it is booked.",
                         inputs=[
                             IterationInputRef(id="in1", kind="build_failure", title="build: compiler errors",
                                               text="src/Core/Svc.cs(3,1): error CS0103 " + "x" * 900,
                                               ref="b1", path="src/Core/Svc.cs"),
                             IterationInputRef(id="in2", kind="uploaded_log", title="ci.log", text="[ERROR] boom"),
                             IterationInputRef(id="in3", kind="parity_defect", title="happy", text="status differs"),
                             IterationInputRef(id="in4", kind="plan_error", title="plan", text="quota"),
                             IterationInputRef(id="in5", kind="test_failure", title="A.B.Fails", text="1 != 2"),
                         ])])


def process_node() -> ScopedDelta:
    return ScopedDelta(scope="estate", nodes=[MemNode(label="Process", uid=f"process:{IR}", name="DealerFundingRequest",
                                                      text="Dealer funding request")])


async def revisions_for_iteration(graph, project: str, n: int) -> list[int]:
    return [r.n for r in await graph.revisions(project, iteration=n)]


def test_projector_writes_requirement_errors_and_lineage():
    [delta] = project_iteration(sample_ingest(edit_set=[EditedFile(path="src/Core/Svc.cs", change="modified"),
                                                        EditedFile(path="old.cs", change="deleted")]))
    assert delta.scope == "iteration:2"
    nodes = {n.uid: n for n in delta.nodes}
    chunk = nodes[REQUIREMENT_UID]
    assert chunk.label == "Chunk" and chunk.props["kind"] == "requirement" and "second approver" in chunk.text
    assert chunk.props["iteration"] == 2 and chunk.props["artifact_type"] == "iteration"
    kinds = {uid.rsplit(":", 1)[-1]: n.props["kind"] for uid, n in nodes.items() if n.label == "Error"}
    assert kinds == {"in1": "build", "in2": "uploaded_log", "in3": "parity", "in4": "deploy", "in5": "test"}
    assert len(nodes[f"error:iteration:{IR}:in1"].props["message"]) == 500
    rels = {r.uid for r in delta.rels}
    assert f"ABOUT:{REQUIREMENT_UID}->process:{IR}" in rels
    assert f"FAILED_AT:error:iteration:{IR}:in1->codefile:dotnet:{IR}/src/Core/Svc.cs" in rels
    assert not any(r.startswith(f"FAILED_AT:error:iteration:{IR}:in2") for r in rels)
    note = nodes[f"memory:edit-set:{IR}:2"]
    assert note.label == "Memory" and note.props["kind"] == "note" and note.props["source"] == "edit set"
    assert f"ABOUT:memory:edit-set:{IR}:2->codefile:dotnet:{IR}/src/Core/Svc.cs" in rels
    assert not any("old.cs" in r for r in rels)


def test_requirement_chunk_is_left_out_without_requirements():
    ingest = sample_ingest()
    ingest.processes[0].requirements = "  "
    [delta] = project_iteration(ingest)
    assert REQUIREMENT_UID not in {n.uid for n in delta.nodes}


def test_hand_origin_passes_through_the_build_projector():
    assert BuildFileInput(path="a.cs", origin="hand").origin == "hand"


def test_prefer_orders_the_iteration_first_and_keeps_scores():
    def hit(uid: str, score: float, it: int | None) -> MemoryHit:
        return MemoryHit.model_validate({
            "uid": uid, "label": "Chunk", "kind": "rule", "text": uid, "score": score, "keyword_score": score,
            "vector_score": 0, "props": {} if it is None else {"iteration": it},
            "citation": {"uid": uid, "label": "Chunk", "phase": "analysis", "revision": 1,
                         "artifact_type": "analysis"}})

    hits = [hit("a", 0.9, None), hit("b", 0.8, 2), hit("c", 0.7, 1), hit("d", 0.6, 2)]
    assert [h.uid for h in prefer(hits, 2)] == ["b", "d", "a", "c"]
    assert [h.score for h in prefer(hits, 2)] == [0.8, 0.6, 0.9, 0.7]


async def test_iteration_revision_holds_requirement_and_errors(memory_graph):
    await memory_graph.apply_revision(meta("estate"), [process_node()])
    r = await memory_graph.apply_revision(meta("it2", phase="iteration", iteration=2), project_iteration(sample_ingest(n=2)))
    assert r.revision is not None
    hits = await memory_graph.subgraph(P, uid=REQUIREMENT_UID)
    node = next(n for n in hits.nodes if n.uid == REQUIREMENT_UID)
    assert node.props["kind"] == "requirement"
    assert await revisions_for_iteration(memory_graph, P, 2) == [r.revision]
    assert await revisions_for_iteration(memory_graph, P, 1) == [r.revision - 1]
    assert [x.iteration for x in await memory_graph.revisions(P)] == [2, 1]


async def test_retrieval_prefers_the_iteration_within_the_floor(memory_graph):
    await memory_graph.apply_revision(meta("estate"), [process_node()])
    older = ScopedDelta(scope="analysis:x", nodes=[MemNode(
        label="Chunk", uid=f"chunk:{IR}:rule:R1", name="rule R1",
        text="Funding over 50k needs a second approver before it is booked, always",
        props={"kind": "rule"})])
    await memory_graph.apply_revision(meta("an", phase="analysis"), [older])
    await memory_graph.apply_revision(meta("it2", phase="iteration", iteration=2), project_iteration(sample_ingest()))
    plain = await hybrid(memory_graph, P, "second approver funding booked", labels=("Chunk",))
    preferred = await hybrid(memory_graph, P, "second approver funding booked", labels=("Chunk",), prefer_iteration=2)
    assert {h.uid for h in plain} == {h.uid for h in preferred}
    assert preferred[0].uid == REQUIREMENT_UID
    assert await hybrid(memory_graph, P, "capital of France", labels=("Chunk",), prefer_iteration=2) == []


async def test_secrets_never_reach_memory(memory_graph):  # review M1
    ingest = sample_ingest()
    ingest.processes[0].requirements = "Use password=hunter2 for the second approver"
    ingest.processes[0].inputs[1].text = "[ERROR] Authorization: Bearer abc.def.ghi refused"
    ingest.processes[0].inputs[1].title = "token ghp_secret123456"
    await memory_graph.apply_revision(meta("estate"), [process_node()])
    await memory_graph.apply_revision(meta("it2", phase="iteration", iteration=2), project_iteration(ingest))
    sub = await memory_graph.subgraph(P, uid=f"process:{IR}", depth=1)
    dump = repr([(n.name, n.text, n.props) for n in sub.nodes])
    assert "hunter2" not in dump and "abc.def.ghi" not in dump and "ghp_secret123456" not in dump
    assert "second approver" in dump
