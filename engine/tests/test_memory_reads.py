import pytest

from engine.memory import cypher
from engine.memory.model import OPEN_VALID_TO, Subgraph
from engine.tests.memory_helpers import (
    P,
    apply,
    build_delta,
    curation_delta,
    estate,
    explain,
    memory_item,
    names,
    query,
)

pytestmark = pytest.mark.usefixtures("hash_embeddings")

REJECTED = "MIGRATED_TO:activity:p0/a0->method:dotnet:p0/Api/Flow.A0"


async def test_as_of_returns_the_graph_of_that_revision(memory_graph):
    await apply(memory_graph, "k1", estate(2))
    await apply(memory_graph, "k2", estate(2, vanished={"p1"}))
    assert names(await memory_graph.subgraph(P, uid="process:p1", as_of=1)) >= {"p1"}
    assert await memory_graph.subgraph(P, uid="process:p1") == Subgraph(
        nodes=[], edges=[], truncated=False, total=0)


async def test_node_detail_lists_versions_with_changed_keys(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k2", estate(1, changed={"p0"}))
    d = await memory_graph.node_detail(P, "process:p0")
    assert [v.valid_from for v in d.versions] == [2, 1]
    assert d.versions[0].changed_keys == ["complexity_band"]


async def test_diff_reports_added_changed_removed_without_reattachments(memory_graph):
    await apply(memory_graph, "k1", estate(3))
    await apply(memory_graph, "k2", estate(3, changed={"p0"}, vanished={"p2"}, added={"p9"}))
    d = await memory_graph.diff(P, 2)
    assert [x.uid for x in d.nodes.changed] == ["process:p0"]
    assert "process:p2" in [x.uid for x in d.nodes.removed]
    assert "process:p9" in [x.uid for x in d.nodes.added]
    assert not [r for r in d.relationships.changed if r.uid.startswith("CONTAINS:process:p0")]


async def test_subgraph_truncates_at_limit(memory_graph):
    await apply(memory_graph, "k1", estate(1, activities=50))
    g = await memory_graph.subgraph(P, uid="process:p0", limit=10)
    assert len(g.nodes) == 10 and g.truncated is True and g.total == 51


async def test_rejected_mapping_reads_as_rejected(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k2", build_delta("p0"))
    await apply(memory_graph, "k3", curation_delta("reject_mapping", target=REJECTED))
    lin = (await memory_graph.node_detail(P, "activity:p0/a0")).lineage
    assert [x.edge.status for x in lin if x.edge.type == "MIGRATED_TO"] == ["rejected"]


async def test_reads_never_cross_projects(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k1", estate(1), project="other")
    assert await memory_graph.node_detail("third", "process:p0") is None


# ------------------------------------------------------------------ beyond the brief
async def test_open_versions_read_as_none_and_internal_props_are_hidden(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    n = await memory_graph.current_node(P, "process:p0")
    assert (n.label, n.name, n.side, n.valid_from, n.valid_to) == ("Process", "Process 0", "old", 1,
                                                                    None)
    assert n.props == {"module": "orders", "complexity_band": "low"} and "Process 0" in n.text
    assert await memory_graph.current_node(P, "process:nope") is None


async def test_subgraph_edges_are_the_valid_relationships_among_its_nodes(memory_graph):
    await apply(memory_graph, "k1", estate(2))
    g = await memory_graph.subgraph(P, uid="process:p0")
    assert g.nodes[0].uid == "process:p0" and g.total == 4 and g.truncated is False
    assert {(e.type, e.src, e.dst) for e in g.edges} == {
        ("CONTAINS", "process:p0", f"activity:p0/a{j}") for j in range(3)}
    assert all(e.status is None for e in g.edges)


async def test_subgraph_filters_neighbours_by_label_and_side(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k2", build_delta("p0"))
    g = await memory_graph.subgraph(P, uid="activity:p0/a0", depth=2, labels=["Test"])
    assert names(g) == {"p0/a0", "dotnet:p0/A0_maps"}
    g = await memory_graph.subgraph(P, uid="activity:p0/a0", depth=2, side="old")
    assert names(g) == {"p0/a0", "p0", "p0/a1", "p0/a2"}
    with pytest.raises(ValueError):
        await memory_graph.subgraph(P, uid="activity:p0/a0", depth=3)
    with pytest.raises(ValueError):
        await memory_graph.subgraph(P, labels=["Nope"])


async def test_subgraph_without_uid_lists_top_level_nodes(memory_graph):
    from engine.memory.model import MemNode, MemRel, ScopedDelta
    delta = estate(1)
    delta.nodes.append(MemNode(label="Domain", uid="domain:orders", name="Orders"))
    delta.rels.append(MemRel(type="CONTAINS", src="domain:orders", dst="process:p0"))
    await apply(memory_graph, "k1", [delta, ScopedDelta(scope="build:p0", nodes=[
        MemNode(label="Solution", uid="solution:dotnet:p0", name="Orders.sln")])])
    g = await memory_graph.subgraph(P)
    assert [n.uid for n in g.nodes] == ["domain:orders", "solution:dotnet:p0"] and g.total == 2
    assert (await memory_graph.subgraph(P, side="new")).total == 1
    assert (await memory_graph.subgraph("other")).total == 0


async def test_lineage_chain_follows_mappings_tests_and_contains_down_from_a_process(
        memory_graph):
    await apply(memory_graph, "k1", estate(2))
    await apply(memory_graph, "k2", build_delta("p0", activities=2))
    g = await memory_graph.lineage_chain(P, "process:p0")
    assert names(g) == {"p0", "p0/a0", "p0/a1", "p0/a2", "dotnet:p0/Api/Flow.A0",
                        "dotnet:p0/Api/Flow.A1", "dotnet:p0/A0_maps", "dotnet:p0/A1_maps"}
    assert g.nodes[0].uid == "process:p0" and g.total == 8
    assert {e.type for e in g.edges} == {"CONTAINS", "MIGRATED_TO", "TESTS"}
    from_method = await memory_graph.lineage_chain(P, "method:dotnet:p0/Api/Flow.A0")
    # back over MIGRATED_TO to the activity, never up CONTAINS to its process
    assert names(from_method) == {"dotnet:p0/Api/Flow.A0", "p0/a0", "dotnet:p0/A0_maps"}
    assert (await memory_graph.lineage_chain(P, "process:p0", as_of=1)).total == 4


async def test_node_detail_about_lineage_and_curations(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k2", build_delta("p0"))
    await apply(memory_graph, "k3", curation_delta("reject_mapping", target=REJECTED),
                phase="curation")
    d = await memory_graph.node_detail(P, "activity:p0/a0")
    assert d.node.curated is True
    assert [n.uid for n in d.about] == ["curation:ev1"]
    assert [n.uid for n in d.curations] == ["curation:ev1"]
    ((lin,),) = [[x for x in d.lineage if x.edge.type == "MIGRATED_TO"]]
    assert (lin.direction, lin.other.uid) == ("out", "method:dotnet:p0/Api/Flow.A0")
    before = await memory_graph.node_detail(P, "activity:p0/a0", as_of=2)
    assert before.node.curated is False and before.curations == []
    assert [x.edge.status for x in before.lineage if x.edge.type == "MIGRATED_TO"] == ["generated"]
    method = await memory_graph.node_detail(P, "method:dotnet:p0/Api/Flow.A0")
    assert sorted((x.edge.type, x.direction) for x in method.lineage) == [
        ("MIGRATED_TO", "in"), ("TESTS", "in")]


async def test_obsolete_memory_reads_as_obsolete(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k2", memory_item("mem_1", "Map JDBC Query to Dapper",
                                                  about="process:p0"), phase="decision")
    await apply(memory_graph, "k3", curation_delta("obsolete", target="mem_1"), phase="curation")
    d = await memory_graph.node_detail(P, "mem_1")
    assert d.node.obsolete is True and d.node.curated is True
    assert [n.uid for n in d.curations] == ["curation:ev1"]
    about = (await memory_graph.node_detail(P, "process:p0")).about
    assert [(n.uid, n.obsolete) for n in about] == [("mem_1", True)]
    assert (await memory_graph.current_node(P, "mem_1")).obsolete is True
    assert (await memory_graph.node_detail(P, "mem_1", as_of=2)).node.obsolete is False


async def test_node_detail_as_of_shows_history_up_to_that_revision(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k2", estate(1, changed={"p0"}))
    d = await memory_graph.node_detail(P, "process:p0", as_of=1)
    assert (d.node.valid_from, d.node.valid_to) == (1, 2)
    assert [(v.valid_from, v.valid_to, v.phase, v.actor) for v in d.versions] == [
        (1, 2, "discover", "tester")]
    assert d.versions[0].changed_keys == [] and d.versions[0].at is not None


async def test_revisions_newest_first_with_paging(memory_graph):
    for i in range(3):
        await apply(memory_graph, f"k{i}", estate(1, changed={"p0"} if i % 2 else set()))
    revs = await memory_graph.revisions(P)
    assert [r.n for r in revs] == [3, 2, 1]
    assert (revs[-1].phase, revs[-1].scopes, revs[-1].actor, revs[-1].added) == (
        "discover", ["estate"], "tester", 4 + 3)
    assert [r.n for r in await memory_graph.revisions(P, before=3, limit=1)] == [2]
    assert (await memory_graph.latest_revision(P)).n == 3
    assert await memory_graph.latest_revision("other") is None
    assert await memory_graph.revisions("other") == []


async def test_diff_counts_relationships_and_unknown_revision_is_none(memory_graph):
    await apply(memory_graph, "k1", estate(2))
    await apply(memory_graph, "k2", estate(2, vanished={"p1"}))
    d = await memory_graph.diff(P, 2)
    assert d.revision.n == 2 and d.nodes.added == [] and d.nodes.changed == []
    assert len(d.nodes.removed) == 4 and len(d.relationships.removed) == 3
    assert {r.kind for r in d.relationships.removed} == {"CONTAINS"}
    first = await memory_graph.diff(P, 1)
    assert (len(first.nodes.added), len(first.relationships.added)) == (8, 6)
    assert await memory_graph.diff(P, 9) is None and await memory_graph.diff("other", 1) is None


async def test_closed_versions_record_the_revision_that_closed_them(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await apply(memory_graph, "k2", build_delta("p0"))
    await apply(memory_graph, "k3", estate(1, changed={"p0"}, activities=0))
    rows = await query(memory_graph, "MATCH (n:Mem {project_id: $project_id}) "
                       "RETURN n.valid_to AS to, n.closed_in AS closed", project_id=P)
    rows += await query(memory_graph, "MATCH ()-[r {project_id: $project_id}]->() "
                        "RETURN r.valid_to AS to, r.closed_in AS closed", project_id=P)
    assert all(r["closed"] == (None if r["to"] == OPEN_VALID_TO else r["to"]) for r in rows)
    assert sum(r["closed"] == 3 for r in rows) == 1 + 3 + 3 + 1  # p0, a*, CONTAINS, MIGRATED_TO


_READS = [
    cypher.NODE_AT, cypher.NEIGHBOURHOOD[1], cypher.NEIGHBOURHOOD[2], cypher.EDGES_AMONG,
    cypher.LINEAGE, cypher.NODE_VERSIONS, cypher.ABOUT_SOURCES, cypher.LINEAGE_EDGES,
    cypher.CURATIONS_OF, cypher.CURATION_TARGETS, cypher.CURATED, cypher.NODES_OPENED_AT,
    cypher.NODES_CLOSED_AT, cypher.RELS_OPENED_AT, cypher.RELS_CLOSED_AT, cypher.REVISION_AT,
    cypher.REVISIONS_BEFORE,
]


@pytest.mark.parametrize("statement", _READS)
async def test_read_statements_seek_an_index(memory_graph, statement):  # rulings R25
    await apply(memory_graph, "k1", estate(3))
    plan = await explain(memory_graph, statement, project_id=P, uid="process:p0", k=1,
                         labels=None, side=None, limit=10, vids=["x"], uids=["x"], n=1,
                         before=5)
    assert "Seek" in plan and "Scan" not in plan, plan


async def test_top_level_read_scans_only_its_three_labels(memory_graph):
    await apply(memory_graph, "k1", estate(3))
    plan = await explain(memory_graph, cypher.TOP_LEVEL, project_id=P, k=1, labels=None,
                         side=None, limit=10)
    scans = [line.strip() for line in plan.splitlines() if "Scan" in line]
    assert scans and all("LabelsScan" in s or "LabelScan" in s for s in scans), plan


def test_every_read_statement_constrains_the_project():
    for statement in [*_READS, cypher.TOP_LEVEL]:
        assert "$project_id" in statement
