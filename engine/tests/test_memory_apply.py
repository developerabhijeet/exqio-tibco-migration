import asyncio

import pytest

from engine.memory import cypher
from engine.memory.model import KnowledgeError, MemNode, MemRel, ScopedDelta
from engine.tests.memory_helpers import (
    P,
    about_chunk,
    build_methods,
    chunk_with,
    current_rel_count,
    estate,
    explain,
    meta,
    node_versions,
    revision_count,
    revisions,
    set_embedding_backend,
    stored_text,
)

pytestmark = pytest.mark.usefixtures("hash_embeddings")


async def test_first_apply_creates_revision_one(memory_graph):
    r = await memory_graph.apply_revision(meta("k1"), [estate(2)])
    assert (r.revision, r.added, r.changed, r.removed) == (1, 2 + 2 * 3 + 2 * 3, 0, 0)  # nodes + rels
    assert r.embedded == 2 + 2 * 3


async def test_unchanged_reapply_creates_nothing(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    r = await memory_graph.apply_revision(meta("k2"), [estate(2)])
    assert r.revision is None and await revision_count(memory_graph, P) == 1


async def test_same_ingest_key_is_skipped(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    r = await memory_graph.apply_revision(meta("k1"), [estate(2, changed={"p0"})])
    assert r.skipped is True and r.revision == 1


async def test_change_closes_old_version_and_reattaches_other_scopes(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    await memory_graph.apply_revision(meta("k2", phase="analysis"), [about_chunk("p0")])
    r = await memory_graph.apply_revision(meta("k3"), [estate(2, changed={"p0"})])
    assert r.revision == 3 and r.changed == 1 and r.reattached >= 4  # 3 CONTAINS + 1 ABOUT
    versions = await node_versions(memory_graph, P, "process:p0")
    assert [(v["valid_from"], v["valid_to"]) for v in versions] == [(1, 3), (3, None)]
    assert [v["phase"] for v in versions] == ["discover", "discover"]
    assert await current_rel_count(memory_graph, P, "ABOUT", dst="process:p0") == 1
    assert await current_rel_count(memory_graph, P, "CONTAINS", src="process:p0") == 3
    assert r.reattached == 4  # each relationship moved exactly once


async def test_relationship_between_two_re_versioned_nodes_moves_once(memory_graph):
    def with_call(delta):
        delta.rels.append(MemRel(type="CALLS", src="process:p0", dst="process:p1"))
        return delta

    await memory_graph.apply_revision(meta("k1"), [with_call(estate(2))])
    r = await memory_graph.apply_revision(meta("k2"), [with_call(estate(2, changed={"p0", "p1"}))])
    assert (r.changed, r.reattached) == (2, 6 + 1)
    assert await current_rel_count(memory_graph, P, "CALLS", src="process:p0", dst="process:p1") == 1


async def test_vanished_node_closes_its_relationships_from_every_scope(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    await memory_graph.apply_revision(meta("k2", phase="analysis"), [about_chunk("p1")])
    r = await memory_graph.apply_revision(meta("k3"), [estate(2, vanished={"p1"})])
    assert r.removed >= 1 + 3 + 3
    assert r.removed == 4 + 3 + 1  # exactly: p1 and its activities, 3 CONTAINS, 1 ABOUT
    assert await current_rel_count(memory_graph, P, "ABOUT", dst="process:p1") == 0


async def test_vanished_activity_closes_its_migrated_to(memory_graph):  # rulings R24
    await memory_graph.apply_revision(meta("k1"), [estate(1)])
    await memory_graph.apply_revision(meta("k2", phase="build"), [build_methods("p0", 2)])
    assert await current_rel_count(memory_graph, P, "MIGRATED_TO", src="activity:p0/a2") == 1
    r = await memory_graph.apply_revision(meta("k3"), [estate(1, activities=2)])
    assert r.removed == 1 + 1 + 1  # the activity, its CONTAINS, its MIGRATED_TO
    assert await current_rel_count(memory_graph, P, "MIGRATED_TO") == 0


async def test_rel_to_missing_uid_is_counted_dangling(memory_graph):
    r = await memory_graph.apply_revision(meta("k1", phase="analysis"), [about_chunk("nope")])
    assert r.dangling == 1 and r.added == 1


async def test_dangling_rel_is_created_once_its_endpoint_exists(memory_graph):
    await memory_graph.apply_revision(meta("k1", phase="analysis"), [about_chunk("p0")])
    await memory_graph.apply_revision(meta("k2"), [estate(1)])
    r = await memory_graph.apply_revision(meta("k3", phase="analysis"), [about_chunk("p0")])
    assert (r.revision, r.added, r.dangling) == (3, 1, 0)
    assert await current_rel_count(memory_graph, P, "ABOUT", dst="process:p0") == 1


async def test_embedding_recomputed_only_when_text_changes(memory_graph, embed_spy):
    await memory_graph.apply_revision(meta("k1"), [estate(1)])
    embed_spy.reset()
    r = await memory_graph.apply_revision(meta("k2"), [estate(1, changed={"p0"}, text_same=True)])
    assert embed_spy.texts == [] and r.changed == 1 and r.embedded == 0
    old, new = await node_versions(memory_graph, P, "process:p0")
    assert new["embedding"] == old["embedding"] and len(new["embedding"]) == 384  # R24: copied


async def test_changed_text_is_re_embedded(memory_graph, embed_spy):
    await memory_graph.apply_revision(meta("k1"), [estate(1)])
    embed_spy.reset()
    r = await memory_graph.apply_revision(meta("k2"), [estate(1, changed={"p0"})])
    assert len(embed_spy.texts) == 1 and "audit" in embed_spy.texts[0] and r.embedded == 1


async def test_preset_embedding_is_stored_without_embedding(memory_graph, embed_spy):
    node = MemNode(label="Memory", uid="mem_000000000001", name="m", text="a note",
                   props={"kind": "note"}, embedding=[1.0] + [0.0] * 383)
    r = await memory_graph.apply_revision(meta("k1", phase="migration"),
                                          [ScopedDelta(scope="migration:orphan", nodes=[node])])
    assert r.embedded == 0 and embed_spy.texts == []
    (v,) = await node_versions(memory_graph, P, "mem_000000000001")
    assert v["embedding"][0] == 1.0


async def test_masked_text_is_what_neo4j_holds(memory_graph):
    await memory_graph.apply_revision(meta("k1", phase="analysis"), [chunk_with("SSN 123-45-6789")])
    assert "123-45-6789" not in await stored_text(memory_graph, P, "chunk:p0:summary")


async def test_raw_xml_text_is_refused_and_counted(memory_graph):
    r = await memory_graph.apply_revision(meta("k1", phase="analysis"),
                                          [chunk_with("<pd:activity name='x'/>")])
    assert r.refused == 1 and r.added == 1 and r.embedded == 0
    assert await stored_text(memory_graph, P, "chunk:p0:summary") == ""


async def test_unknown_label_is_rejected(memory_graph):
    bad = MemNode.model_construct(label="Bogus", uid="x", name="x", text="", props={},
                                  embedding=None)
    with pytest.raises(ValueError, match="Bogus"):
        await memory_graph.apply_revision(meta("k1"), [ScopedDelta(scope="estate", nodes=[bad])])
    assert await revision_count(memory_graph, P) == 0


async def test_other_embedding_backend_is_refused(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(1)])
    await set_embedding_backend(memory_graph, "x")
    with pytest.raises(KnowledgeError, match="'x'"):
        await memory_graph.apply_revision(meta("k2"), [estate(1, changed={"p0"})])
    assert await revision_count(memory_graph, P) == 1
    assert [(v["valid_from"], v["valid_to"])
            for v in await node_versions(memory_graph, P, "process:p0")] == [(1, None)]
    assert (await memory_graph.counts(P))["Activity"] == 3


def _table(uid: str = "table:orders") -> MemNode:
    return MemNode(label="Table", uid=uid, name="orders", text="orders table")


async def test_item_moving_between_scopes_of_one_apply_is_handed_over(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [ScopedDelta(scope="a", nodes=[_table()])])
    r = await memory_graph.apply_revision(
        meta("k2"), [ScopedDelta(scope="a"), ScopedDelta(scope="b", nodes=[_table()])])
    assert (r.revision, r.added, r.changed, r.removed) == (2, 0, 1, 0)
    assert (await memory_graph.counts(P))["Table"] == 1
    old, new = await node_versions(memory_graph, P, "table:orders")
    assert (old["scope"], old["valid_to"], new["scope"], new["valid_to"]) == ("a", 2, "b", None)


async def test_relationship_moving_between_scopes_of_one_apply_is_handed_over(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(1)])
    rel = MemRel(type="CALLS", src="process:p0", dst="process:p0")
    await memory_graph.apply_revision(meta("k2"), [ScopedDelta(scope="a", rels=[rel])])
    r = await memory_graph.apply_revision(
        meta("k3"), [ScopedDelta(scope="a"), ScopedDelta(scope="b", rels=[rel])])
    assert (r.added, r.changed, r.removed) == (0, 1, 0)
    assert await current_rel_count(memory_graph, P, "CALLS") == 1


async def test_item_owned_by_a_scope_outside_the_apply_is_left_alone(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [ScopedDelta(scope="a", nodes=[_table()])])
    other = _table().model_copy(update={"text": "claimed by b"})
    r = await memory_graph.apply_revision(meta("k2"), [ScopedDelta(scope="b", nodes=[other])])
    assert r.revision is None and await revision_count(memory_graph, P) == 1
    (v,) = await node_versions(memory_graph, P, "table:orders")
    assert (v["scope"], v["valid_to"]) == ("a", None)
    assert await stored_text(memory_graph, P, "table:orders") == "orders table"


async def test_mixed_type_list_property_is_rejected(memory_graph):
    bad = MemNode(label="Table", uid="table:x", name="x", props={"cols": ["a", 1]})
    with pytest.raises(ValueError, match="one type"):
        await memory_graph.apply_revision(meta("k1"), [ScopedDelta(scope="a", nodes=[bad])])


@pytest.mark.parametrize("statement", [
    cypher.CURRENT_IN_SCOPE, cypher.CURRENT_BY_UID, cypher.CURRENT_RELS["ABOUT"],
    cypher.CURRENT_RELS_BY_UID["ABOUT"], cypher.CLOSE_NODES, cypher.CREATE_NODES["Process"],
    *cypher.REATTACH["CONTAINS"], cypher.CLOSE_RELS_OF_CLOSED["CONTAINS"],
    cypher.CREATE_RELS["CONTAINS"], cypher.CLOSE_RELS["CONTAINS"], cypher.COUNTS,
    cypher.REVISION_BY_IKEY, cypher.NEXT_REVISION,
])
async def test_write_path_statements_seek_an_index(memory_graph, statement):  # rulings R25
    await memory_graph.apply_revision(meta("k1"), [estate(3)])
    plan = await explain(memory_graph, statement, project_id=P, scopes=["estate"], uids=["x"],
                         vids=["x"], rows=[], n=2, phase="discover", ikey="x")
    assert "Seek" in plan and "Scan" not in plan, plan


async def test_revision_node_records_the_meta(memory_graph):
    m = meta("k1").model_copy(update={"source_job_id": "job-1", "ingest_job_id": "job-2"})
    await memory_graph.apply_revision(m, [estate(1)])
    (r,) = await revisions(memory_graph, P)
    assert (r["n"], r["ikey"], r["phase"], r["scopes"], r["job_id"], r["ingest_job_id"],
            r["actor"], r["added"]) == (1, f"{P}|k1", "discover", ["estate"], "job-1", "job-2",
                                         "tester", 4 + 3)


async def test_projects_are_isolated(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    await memory_graph.apply_revision(meta("k1", project="other"), [estate(1)])
    assert (await memory_graph.counts(P))["Process"] == 2
    assert (await memory_graph.counts("other"))["Process"] == 1


@pytest.mark.slow
async def test_large_first_ingest_fits_one_transaction(memory_graph):  # Review Focus 5, R6
    delta = estate(5000)  # 5 000 processes x (1 + 3 activities) = 20 000 nodes, 15 000 rels
    assert len(delta.nodes) >= 20000
    r = await asyncio.wait_for(memory_graph.apply_revision(meta("k1"), [delta]), 300)
    assert r.revision == 1 and r.added == 20000 + 15000
    assert (await memory_graph.counts(P))["Activity"] == 15000
