"""Projectors of old code, analysis and design (pure), plus one apply against the test Neo4j."""
import json
from pathlib import Path

import pytest

from engine.domains import discover
from engine.domains import graph as graph_mod
from engine.dotnet_gen.models import Decision, DesignRecord, SolutionLayout
from engine.memory.projectors.analysis import ChunkInput, ProcessRef, project_analysis, slug
from engine.memory.projectors.design import project_design
from engine.memory.projectors.estate import DomainInput, project_estate
from engine.memory.search import hybrid
from engine.tests.memory_helpers import P, apply, current_rel_count
from engine.tibco_parser.scanner import scan_folder

GMF = Path(__file__).resolve().parents[2] / "samples" / "synthetic-gmf"


@pytest.fixture(scope="module")
def apps():
    return scan_folder(GMF).applications


@pytest.fixture(scope="module")
def graph(apps):
    return graph_mod.build_graph(apps)


@pytest.fixture(scope="module")
def domains(apps):
    return [DomainInput(key=c.id, name=c.suggested_name, suggested_name=c.suggested_name,
                        ir_process_ids=c.process_ids) for c in discover.discover_domains(apps)]


@pytest.fixture(scope="module")
def refs(apps):
    return [ProcessRef(ir_id=p.id, api_id=f"api-{p.id}", name=p.name)
            for a in apps for p in a.processes]


@pytest.fixture(scope="module")
def ref():
    return ProcessRef(ir_id="proc.One", api_id="api-1", name="One")


def test_estate_has_every_process_and_activity_once(apps, domains, graph, refs):
    d = project_estate(apps, domains, graph, refs)
    uids = [n.uid for n in d.nodes]
    assert len(uids) == len(set(uids)) and d.scope == "estate"
    assert {f"process:{p.id}" for a in apps for p in a.processes} <= set(uids)
    assert all(f"activity:{p.id}/{x.id}" in uids
               for a in apps for p in a.processes for x in p.activities)
    assert len({r.uid for r in d.rels}) == len(d.rels)


def test_estate_is_deterministic(apps, domains, graph, refs):
    assert project_estate(apps, domains, graph, refs) == project_estate(apps, domains, graph, refs)


def test_starter_activity_is_marked(apps, domains, graph, refs):
    nodes = project_estate(apps, domains, graph, refs).nodes
    starters = [n for n in nodes if n.label == "Activity" and n.props.get("starter")]
    assert starters and all(
        n.props["starter_kind"] in {"rest", "http", "soap", "jms", "timer", "subprocess"}
        for n in starters)


def test_resource_settings_and_substvar_values_never_projected(apps, domains, graph, refs):
    d = project_estate(apps, domains, graph, refs)
    assert all(set(n.props) <= {"kind", "name", "artifact_type", "artifact_id", "process_id",
                                "path"} for n in d.nodes if n.label == "SharedResource")
    assert all(set(n.props) == {"type"} for n in d.nodes if n.label == "SubstVar")
    # R12: the values themselves, not only the key names, appear nowhere in the delta
    dump = json.dumps([[n.text, n.props] for n in d.nodes
                       if n.label in ("SubstVar", "SharedResource")])
    substvars = [s for a in apps for s in a.substvars]
    resources = [r for a in apps for r in a.resources]
    assert substvars and resources
    for s in substvars:
        if len(s.value) >= 4:
            assert s.value not in dump, f"substvar {s.name} value leaked"
    for r in resources:
        for v in r.settings.values():
            if isinstance(v, str) and len(v) >= 6:
                assert v not in dump, f"resource {r.id} setting leaked"


def test_every_estate_rel_endpoint_is_in_the_delta(apps, domains, graph, refs):
    d = project_estate(apps, domains, graph, refs)
    uids = {n.uid for n in d.nodes}
    assert all(r.src in uids and r.dst in uids for r in d.rels)


def test_estate_graph_edges_and_uses_are_projected(apps, domains, graph, refs):
    d = project_estate(apps, domains, graph, refs)
    types = {r.type for r in d.rels}
    assert {"CONTAINS", "CALLS", "READS", "WRITES", "EXPOSES", "USES"} <= types
    assert any(n.label == "Endpoint" and n.props["side"] == "old" and n.uid.startswith(
        "endpoint:old:") and n.props["method"] and n.props["path"] for n in d.nodes)
    assert any(r.type == "USES" and r.dst.startswith("contract:") for r in d.rels)
    assert any(r.type == "CONTAINS" and r.src.startswith("application:") for r in d.rels)
    assert any(r.type == "CONTAINS" and r.src.startswith("domain:") for r in d.rels)


def test_substvar_references_in_activity_config_become_uses(apps, domains, graph, refs):
    app = next(a for a in apps if a.substvars).model_copy(deep=True)
    proc = next(p for p in app.processes if p.activities)
    proc.activities[0].config["url"] = f"http://h:%%{app.substvars[0].name}%%/x %%Unknown%%"
    d = project_estate([app], [], graph_mod.build_graph([app], []), refs)
    uses = [r for r in d.rels if r.type == "USES" and r.dst.startswith("substvar:")]
    assert [(r.src, r.dst) for r in uses] == [
        (f"process:{proc.id}", f"substvar:{app.id}/{app.substvars[0].name}")]


def test_process_and_activity_carry_text_and_citation_props(apps, domains, graph, refs):
    d = project_estate(apps, domains, graph, refs)
    for n in d.nodes:
        if n.label in ("Process", "Activity"):
            assert n.text and n.props["artifact_type"] == "process"
            assert n.props["artifact_id"].startswith("api-")
    assert all(n.props["path"] for n in d.nodes if n.label == "Process")


def test_analysis_chunks_are_about_process_and_cited_activities(ref):
    d = project_analysis(ref, [ChunkInput(kind="rule", key="R1", text="Rule R1 ...",
                                          activity_ids=["a1"])])
    assert d.scope == f"analysis:{ref.ir_id}" and d.nodes[0].uid == f"chunk:{ref.ir_id}:rule:R1"
    assert {r.dst for r in d.rels} == {f"process:{ref.ir_id}", f"activity:{ref.ir_id}/a1"}
    n = d.nodes[0]
    assert n.props["artifact_type"] == "analysis" and n.props["artifact_id"] == "api-1"


def test_analysis_summary_and_finding_uids(ref):
    d = project_analysis(ref, [ChunkInput(kind="summary", key="summary", text="s"),
                               ChunkInput(kind="finding", key="F2", text="f")])
    assert [n.uid for n in d.nodes] == [f"chunk:{ref.ir_id}:summary",
                                        f"chunk:{ref.ir_id}:finding:F2"]


def _design(*decisions: Decision, narrative: str | None = None) -> DesignRecord:
    return DesignRecord(process_id="proc.One", spec_hash="h", solution=SolutionLayout(name="Sol"),
                        decisions=list(decisions), narrative=narrative)


def test_design_decisions_become_design_chunks(ref):
    rec = _design(Decision(title="Use Dapper", choice="Dapper", alternatives=["EF Core"],
                           reason="simple SQL"))
    d = project_design(ref, rec, "dr1")
    assert d.scope == f"design:{ref.ir_id}"
    assert [n.uid for n in d.nodes] == [f"chunk:{ref.ir_id}:design:decision:use-dapper"]
    assert d.nodes[0].text == "Use Dapper: Dapper. Alternatives: EF Core. Reason: simple SQL"
    assert d.nodes[0].props["artifact_type"] == "design" and d.nodes[0].props["artifact_id"] == "dr1"
    assert [(r.src, r.dst) for r in d.rels] == [(d.nodes[0].uid, f"process:{ref.ir_id}")]


def test_design_narrative_and_duplicate_titles(ref):
    same = Decision(title="Use Dapper", choice="a", reason="r")
    d = project_design(ref, _design(same, same, narrative="The design."), "dr1")
    uids = [n.uid for n in d.nodes]
    assert uids[0] == f"chunk:{ref.ir_id}:design:narrative" and len(set(uids)) == 3
    assert project_design(ref, _design(), "dr1").nodes == []


def test_slug():
    assert slug("Use Dapper, not EF!") == "use-dapper-not-ef"
    assert len(slug("x" * 100)) == 60 and slug("***") == ""


@pytest.mark.usefixtures("hash_embeddings")
async def test_projected_deltas_apply_and_are_searchable(memory_graph, apps, domains, graph, refs):
    one = apps[0].processes[0]
    r1 = ProcessRef(ir_id=one.id, api_id="api-x", name=one.name)
    est = project_estate(apps, domains, graph, refs)
    res = await apply(memory_graph, "k1", [est])
    assert res.revision == 1 and res.dangling == 0 and res.refused == 0
    await apply(memory_graph, "k2", [
        project_analysis(r1, [ChunkInput(kind="summary", key="summary", text="Validates loans",
                                         activity_ids=[one.activities[0].id])]),
        project_design(r1, _design(Decision(title="Use Dapper", choice="Dapper", reason="r")),
                       "dr1")], phase="analysis")
    assert await current_rel_count(memory_graph, P, "ABOUT", dst=f"process:{one.id}") == 2
    assert await current_rel_count(memory_graph, P, "CONTAINS", src=f"process:{one.id}") == len(
        one.activities)
    # unchanged re-apply is a no-op
    assert (await apply(memory_graph, "k3", [est])).revision is None
    # R16: the Process text is searchable and its hit cites the API process id
    hits = await hybrid(memory_graph, P, f"{one.name} starter activities",
                        labels=["Process", "Activity"], min_terms=1, k=50)
    mine = [h for h in hits if h.uid == f"process:{one.id}"]
    assert mine and mine[0].citation.artifact_id == f"api-{one.id}"
    assert mine[0].citation.artifact_type == "process"
