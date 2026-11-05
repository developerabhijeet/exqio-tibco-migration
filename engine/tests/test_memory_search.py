import itertools

import pytest

from engine.memory import cypher
from engine.memory.model import KnowledgeError, MemNode, ScopedDelta
from engine.memory.search import MemoryRetriever, hybrid, query_terms
from engine.tests.memory_helpers import (
    P,
    apply,
    curation_delta,
    estate,
    explain,
    raw_similarity,
    seed_chunks,
    seed_memory,
    set_embedding_backend,
)

pytestmark = pytest.mark.usefixtures("hash_embeddings")  # EMBEDDINGS_BACKEND=hash


async def test_finds_a_rule_by_its_words(memory_graph):
    await seed_chunks(memory_graph, [("rule", "Credit limit above 5000 needs manager approval")])
    hits = await hybrid(memory_graph, P, "credit limit approval")
    assert hits and hits[0].uid == "chunk:p0:rule:R1" and hits[0].citation.artifact_type == "analysis"
    assert hits[0].citation.revision == 1 and hits[0].citation.phase == "analysis"


async def test_unrelated_question_finds_nothing(memory_graph):
    await seed_chunks(memory_graph, [("rule", "Credit limit above 5000 needs manager approval")])
    assert await hybrid(memory_graph, P, "capital of France") == []


async def test_vector_score_is_raw_cosine(memory_graph):  # rulings R11
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    # One camelCase term the full-text index does not split: the keyword half finds nothing,
    # so the hit comes from the vector half alone.
    hit = (await hybrid(memory_graph, P, "LoanPayoffQuote"))[0]
    assert hit.keyword_score == 0 and hit.vector_score == pytest.approx(1.0)
    assert await raw_similarity(memory_graph, "loan payoff quote") > 0.99


async def test_floor_applies_to_raw_cosine_not_neo4j_score(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    raw = await raw_similarity(memory_graph, "LoanPayoffQuote")  # about 0.86
    # Neo4j's own score (1 + raw) / 2 would clear a floor just above raw; raw cosine does not.
    assert await hybrid(memory_graph, P, "LoanPayoffQuote", vector_floor=raw + 0.01) == []
    assert await hybrid(memory_graph, P, "LoanPayoffQuote", vector_floor=raw - 0.01)


async def test_hostile_query_strings_are_data(memory_graph):  # Review Focus 4
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    for q in ['foo" OR 1=1 //', "name:*", ") MATCH (n) DETACH DELETE n //", "\\", "*:*"]:
        await hybrid(memory_graph, P, q)
    assert (await memory_graph.counts(P))["Chunk"] == 1


async def test_other_projects_never_match(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")], project="other")
    assert await hybrid(memory_graph, P, "loan payoff quote") == []


async def test_obsolete_memory_is_left_out(memory_graph):
    await seed_memory(memory_graph, "mem_1", "Map JDBC Query to Dapper")
    await apply(memory_graph, "k9", curation_delta("obsolete", target="mem_1"), phase="curation")
    assert await hybrid(memory_graph, P, "JDBC Query Dapper", labels=["Memory"]) == []
    assert await hybrid(memory_graph, P, "JDBC Query Dapper", labels=["Memory"], include_obsolete=True)


async def test_obsolete_memory_is_found_before_the_curation(memory_graph):
    await seed_memory(memory_graph, "mem_1", "Map JDBC Query to Dapper")
    await apply(memory_graph, "k9", curation_delta("obsolete", target="mem_1"), phase="curation")
    hits = await hybrid(memory_graph, P, "JDBC Query Dapper", labels=["Memory"], as_of=1)
    assert [h.uid for h in hits] == ["mem_1"]
    assert hits[0].citation.artifact_type == "memory" and hits[0].kind == "decision"


async def test_as_of_searches_old_text(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    await seed_chunks(memory_graph, [("summary", "escrow refund schedule")], key="k2")
    assert await hybrid(memory_graph, P, "loan payoff quote") == []
    assert await hybrid(memory_graph, P, "loan payoff quote", as_of=1)


def test_query_terms_keeps_identifiers_and_drops_lucene_stopwords():  # rulings R2
    assert query_terms("Is num_of_pulls NOT they OR x") == ["num_of_pulls", "x"]


async def test_min_terms_needs_that_many_matching_terms(memory_graph):
    await seed_chunks(memory_graph, [("rule", "Credit limit above 5000 needs manager approval")])
    hits = await hybrid(memory_graph, P, "credit escrow", vector_floor=1.0)  # keyword half only
    assert hits == []  # one of two terms matches; min_terms=2 needs both
    hits = await hybrid(memory_graph, P, "credit escrow", vector_floor=1.0, min_terms=1)
    assert [h.uid for h in hits] == ["chunk:p0:rule:R1"] and hits[0].vector_score == 0


async def test_hits_combine_both_halves_and_sort_by_score(memory_graph):
    await seed_chunks(memory_graph, [
        ("rule", "Credit limit above 5000 needs manager approval"),
        ("rule", "Credit limit is checked nightly"),
        ("summary", "escrow refund schedule"),
    ])
    hits = await hybrid(memory_graph, P, "credit limit approval")
    assert [h.uid for h in hits][:2] == ["chunk:p0:rule:R1", "chunk:p0:rule:R2"]
    top = hits[0]
    assert top.keyword_score == pytest.approx(1.0) and top.vector_score == pytest.approx(1.0)
    assert top.score == pytest.approx(0.5 * top.keyword_score + 0.5 * top.vector_score)
    assert all(a.score >= b.score for a, b in itertools.pairwise(hits))
    assert len(await hybrid(memory_graph, P, "credit limit approval", k=1)) == 1


async def test_labels_side_and_phase_filter(memory_graph):
    await apply(memory_graph, "k1", estate(1))
    await seed_chunks(memory_graph, [("summary", "Process 0 receives and validates orders")],
                      key="k2")
    q = "process receive validate"
    assert {h.label for h in await hybrid(memory_graph, P, q, labels=["Process", "Chunk"])} == {
        "Process", "Chunk"}
    assert {h.label for h in await hybrid(memory_graph, P, q, labels=["Process", "Chunk"],
                                          side="old")} == {"Process"}
    assert {h.label for h in await hybrid(memory_graph, P, q, labels=["Process", "Chunk"],
                                          phase="analysis")} == {"Chunk"}
    with pytest.raises(ValueError):
        await hybrid(memory_graph, P, q, labels=["Nope"])


async def test_ann_path_post_filters_by_project_and_validity(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")], project="other")
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    await seed_chunks(memory_graph, [("summary", "escrow refund schedule")], key="k2")
    memory_graph.exact_scan_max = 0  # every label uses its vector index
    assert await hybrid(memory_graph, P, "LoanPayoffQuote") == []
    hits = await hybrid(memory_graph, P, "LoanPayoffQuote", as_of=1)
    assert [h.uid for h in hits] == ["chunk:p0:summary"] and hits[0].citation.revision == 1
    assert hits[0].keyword_score == 0


async def test_mixed_embedding_backends_are_refused(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    await set_embedding_backend(memory_graph, "fastembed:other")
    with pytest.raises(KnowledgeError):
        await hybrid(memory_graph, P, "loan payoff quote")


async def test_blank_query_and_zero_k_find_nothing(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    assert await hybrid(memory_graph, P, "   ") == []
    assert await hybrid(memory_graph, P, "loan payoff quote", k=0) == []


async def test_memory_graph_is_a_retriever(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    retriever: MemoryRetriever = memory_graph
    hits = await retriever.retrieve(P, "loan payoff quote")
    assert [h.uid for h in hits] == ["chunk:p0:summary"]
    assert isinstance(memory_graph, MemoryRetriever)


async def test_citation_falls_back_when_projector_props_are_missing(memory_graph):
    await apply(memory_graph, "k1", ScopedDelta(scope="decision:m2", nodes=[MemNode(
        label="Memory", uid="m2", name="decision", text="Use Dapper for JDBC queries")]),
        phase="decision")
    (hit,) = await hybrid(memory_graph, P, "Dapper JDBC queries", labels=["Memory"])
    assert (hit.citation.artifact_type, hit.citation.artifact_id) == ("memory", None)
    assert hit.kind is None and hit.citation.process_id is None


async def test_endpoint_citation_falls_back_by_its_side(memory_graph):
    def endpoint(uid, side, text):
        return MemNode(label="Endpoint", uid=uid, name=uid, text=text, props={"side": side})

    await apply(memory_graph, "k1", ScopedDelta(scope="estate", nodes=[
        endpoint("endpoint:old:quote", "old", "GET payoff quote endpoint"),
        endpoint("endpoint:new:quote", "new", "GET payoff quote endpoint")]))
    hits = await hybrid(memory_graph, P, "payoff quote endpoint", labels=["Endpoint"])
    assert {h.uid: h.citation.artifact_type for h in hits} == {
        "endpoint:old:quote": "process", "endpoint:new:quote": "build"}
    assert all(h.citation.artifact_id is None for h in hits)


async def test_full_text_overfetch_still_finds_this_project(memory_graph, monkeypatch):
    from engine.memory import search
    # Shorter texts score higher: the other project's five fill a limit of 3 per term.
    for i in range(5):
        await seed_chunks(memory_graph, [("summary", "loan payoff")], project="other",
                          process_key=f"o{i}", key=f"k{i}")
    await seed_chunks(memory_graph, [("summary", "loan payoff quote for the account holder")])
    monkeypatch.setattr(search, "FT_OVERFETCH", 3)
    hits = await hybrid(memory_graph, P, "loan payoff", vector_floor=1.0)  # keyword half only
    assert [h.uid for h in hits] == ["chunk:p0:summary"] and hits[0].keyword_score == 1.0


async def test_ann_retries_when_other_vectors_crowd_the_candidates(memory_graph, monkeypatch):
    from engine.memory import search
    monkeypatch.setattr(search, "ANN_CANDIDATES_PER_K", 10)  # keeps the crowd small
    # 30 of another project's vectors are nearer the query than this project's one chunk,
    # more than the first 10 * k candidates. Distinct vectors (cosine about 0.92 against
    # 0.86): HNSW returns few of many duplicates.
    await seed_chunks(memory_graph, [("summary", f"LoanPayoffQuote q{i}") for i in range(30)],
                      project="other")
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    memory_graph.exact_scan_max = 0
    hits = await hybrid(memory_graph, P, "LoanPayoffQuote", k=1)
    assert [h.uid for h in hits] == ["chunk:p0:summary"] and hits[0].keyword_score == 0


async def test_query_is_not_embedded_without_an_embedded_label(memory_graph, embed_spy):
    await apply(memory_graph, "k1", ScopedDelta(scope="estate", nodes=[
        MemNode(label="Table", uid="table:loans", name="loans", text="loan payoff table")]))
    embed_spy.reset()
    hits = await hybrid(memory_graph, P, "loan payoff", labels=["Table"])
    assert [h.uid for h in hits] == ["table:loans"] and embed_spy.texts == []


_PARAMS = {"project_id": P, "k": 1, "labels": ["Chunk", "Memory"], "side": None,
           "phase": None, "include_obsolete": False, "terms": ["loan"], "need": 1, "n": 50,
           "v": [0.1] * 384, "floor": 0.25, "candidates": 800,
           "unbounded": ["loan"], "ft_limit": 2000}


async def test_exact_vector_scan_reads_only_the_project(memory_graph):
    await apply(memory_graph, "k1", estate(3))
    for statement in (cypher.SEARCH_VECTOR_EXACT, cypher.SEARCH_EMBEDDED_COUNTS):
        plan = await explain(memory_graph, statement, **_PARAMS)
        assert "NodeIndexSeek" in plan and "Scan" not in plan, plan
        assert "project_id = $project_id" in plan, plan


async def test_keyword_and_ann_statements_start_from_their_index(memory_graph):
    await apply(memory_graph, "k1", estate(3))
    for statement in (cypher.SEARCH_KEYWORD, *cypher.SEARCH_VECTOR_ANN.values()):
        plan = await explain(memory_graph, statement, **_PARAMS)
        assert "ProcedureCall" in plan and "Scan" not in plan, plan
    # The obsolete check seeks the curation target index.
    assert "target_uid" in await explain(memory_graph, cypher.SEARCH_KEYWORD, **_PARAMS)


def test_every_search_statement_constrains_the_project():
    for statement in (cypher.SEARCH_KEYWORD, cypher.SEARCH_VECTOR_EXACT,
                      cypher.SEARCH_EMBEDDED_COUNTS, *cypher.SEARCH_VECTOR_ANN.values()):
        assert "$project_id" in statement
