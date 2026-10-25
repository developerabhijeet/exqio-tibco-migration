"""Hybrid search of the memory graph: Neo4j full text plus cosine similarity, each half
normalised by its own best score and then combined, with today's floors.

The keyword half returns only nodes that match at least ``min_terms`` of the query's terms (all
of them when the query has fewer), so one shared common word is not a match. The vector half
returns only nodes whose raw cosine similarity is at least ``vector_floor``. A query that
clears neither floor returns nothing: "capital of France" finds no migration knowledge.

The vector half scans the project's nodes exactly. A label's vector index (approximate, across
every project) serves only labels with more than ``MemoryGraph.exact_scan_max`` current
embedded nodes in the project, with ``100 * k`` candidates post-filtered by project and
validity: Neo4j 5.26 Community cannot filter a vector index query before ranking.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from neo4j import AsyncManagedTransaction

from engine.knowledge import embeddings
from engine.knowledge.embeddings import STOPWORDS
from engine.memory import cypher
from engine.memory.model import (
    EMBEDDED_LABELS,
    KNOWLEDGE_LABELS,
    NODE_LABELS,
    OPEN_VALID_TO,
    SIDES,
    Citation,
    MemoryHit,
    backend_mismatch,
)

if TYPE_CHECKING:
    from engine.memory.graph import MemoryGraph

KEYWORD_WEIGHT = 0.5
VECTOR_WEIGHT = 0.5
CANDIDATES_PER_HALF = 50
ANN_CANDIDATES_PER_K = 100
# ANN retries double the candidates up to this many when the post-filter leaves fewer than k.
ANN_CANDIDATES_MAX = 10000
# Full-text matches fetched per term before the project filter; a term that fills it is
# fetched again without a limit, so results equal an unbounded query.
FT_OVERFETCH = 2000
# Stopwords of Lucene's English analyzer that STOPWORDS lacks: the index drops them, so as
# terms they would count towards the match requirement but never match.
LUCENE_STOPWORDS = frozenset({"no", "not", "they"})


def query_terms(query: str) -> list[str]:
    """Lowercase, de-duplicated search terms (only ``[a-z0-9_]``) without stopwords.
    Identifiers keep their underscores (``num_of_pulls``)."""
    seen: dict[str, None] = {}
    for raw in re.findall(r"[A-Za-z0-9_]+", query):
        t = raw.lower().strip("_")
        if t and t not in STOPWORDS and t not in LUCENE_STOPWORDS:
            seen.setdefault(t, None)
    return list(seen)


async def hybrid(graph: MemoryGraph, project_id: str, query: str, *,
                 labels: Sequence[str] = KNOWLEDGE_LABELS, side: str | None = None,
                 phase: str | None = None, k: int = 8, as_of: int | None = None,
                 min_terms: int = 2, vector_floor: float | None = None,
                 include_obsolete: bool = False) -> list[MemoryHit]:
    """The ``k`` best nodes of ``labels`` for ``query`` as of revision ``as_of`` (current when
    None); ``side`` and ``phase`` filter on the version's properties. ``vector_floor=None``
    uses the active embedding backend's floor (raw cosine). A Memory made obsolete by a
    curation is left out unless ``include_obsolete``."""
    unknown = set(labels) - set(NODE_LABELS)
    if unknown:
        raise ValueError(f"unknown node labels {sorted(unknown)}")
    if side is not None and side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    if not query.strip() or k <= 0 or not labels:
        return []
    floor = embeddings.default_vector_floor() if vector_floor is None else vector_floor
    vector = None
    if any(label in EMBEDDED_LABELS for label in labels):  # else no vector half to embed for
        vector = (await asyncio.to_thread(embeddings.embed, [query]))[0]
    terms = query_terms(query)
    p = {
        "project_id": project_id, "k": OPEN_VALID_TO - 1 if as_of is None else as_of,
        "open": OPEN_VALID_TO, "labels": list(labels), "side": side, "phase": phase,
        "include_obsolete": include_obsolete, "n": CANDIDATES_PER_HALF, "terms": terms,
        "need": min(max(min_terms, 1), len(terms)), "floor": floor,
        "v": vector, "want": k, "ft_limit": FT_OVERFETCH,
    }
    keyword, vec = await graph._read(_halves, p, embeddings.backend_name(), graph.exact_scan_max)
    return _combine(keyword, vec, k)


async def _rows(tx: AsyncManagedTransaction, statement: str, p: dict[str, Any],
                **extra: Any) -> list[dict[str, Any]]:
    return await (await tx.run(statement, {**p, **extra})).data()


async def _halves(tx: AsyncManagedTransaction, p: dict[str, Any], backend: str,
                  exact_scan_max: int) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The keyword and vector candidates (uid -> ``{node, score}``), in one read transaction."""
    meta = await (await tx.run(cypher.META_GET)).single()
    if meta is not None and meta["value"] != backend:
        raise backend_mismatch(meta["value"], backend)
    keyword = await _keyword(tx, p) if p["terms"] else {}
    vector: dict[str, dict[str, Any]] = {}
    embedded = [label for label in p["labels"] if label in EMBEDDED_LABELS]
    if not embedded or p["v"] is None or not any(p["v"]):  # a wordless query: zero vector
        return keyword, vector
    counts = {r["label"]: r["n"] for r in await _rows(tx, cypher.SEARCH_EMBEDDED_COUNTS, p,
                                                      labels=embedded)}
    exact = [label for label in embedded if counts.get(label, 0) <= exact_scan_max]
    rows: list[dict[str, Any]] = []
    if exact:
        rows += await _rows(tx, cypher.SEARCH_VECTOR_EXACT, p, labels=exact)
    for label in embedded:
        if label not in exact:
            rows += await _ann(tx, p, label)
    rows.sort(key=lambda r: (-r["score"], r["node"]["p"]["uid"]))
    for r in rows[:CANDIDATES_PER_HALF]:
        vector.setdefault(r["node"]["p"]["uid"], r)
    return keyword, vector


async def _keyword(tx: AsyncManagedTransaction,
                   p: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The keyword candidates. Each term fetches at most ``ft_limit`` matches across the
    database; a term that filled it is fetched again without a limit (at most one re-run)."""
    unbounded: list[str] = []
    while True:
        row = (await _rows(tx, cypher.SEARCH_KEYWORD, p, unbounded=unbounded))[0]
        full = [r["t"] for r in row["raws"]
                if r["raw"] >= p["ft_limit"] and r["t"] not in unbounded]
        if not full:
            return {h["node"]["p"]["uid"]: h for h in row["hits"]}
        unbounded += full


async def _ann(tx: AsyncManagedTransaction, p: dict[str, Any], label: str) -> list[dict[str, Any]]:
    """The ANN candidates of ``label``: ``100 * k`` nearest, doubled (up to
    ANN_CANDIDATES_MAX) while the post-filter leaves fewer than k, the index returned all it
    was asked for and its last candidate still clears the floor."""
    candidates = ANN_CANDIDATES_PER_K * p["want"]
    cap = max(candidates, min(16 * candidates, ANN_CANDIDATES_MAX))
    while True:
        row = (await _rows(tx, cypher.SEARCH_VECTOR_ANN[label], p, labels=[label],
                           candidates=candidates))[0]
        more = (len(row["hits"]) < p["want"] and row["raw"] >= candidates
                and row["lowest"] >= p["floor"] and candidates < cap)
        if not more:
            return row["hits"]
        candidates = min(2 * candidates, cap)


def _combine(keyword: dict[str, dict[str, Any]], vector: dict[str, dict[str, Any]],
             k: int) -> list[MemoryHit]:
    """Each half normalised by its best score, combined 0.5/0.5, sorted by score then uid."""
    best_kw = max((float(r["score"]) for r in keyword.values()), default=0.0)
    best_vec = max((float(r["score"]) for r in vector.values()), default=0.0)
    out: list[MemoryHit] = []
    for uid in keyword.keys() | vector.keys():
        node = (keyword.get(uid) or vector[uid])["node"]
        kw = float(keyword[uid]["score"]) if uid in keyword else 0.0
        vc = float(vector[uid]["score"]) if uid in vector else 0.0
        kw_norm = (kw / best_kw) if best_kw > 0 else (1.0 if uid in keyword else 0.0)
        vc_norm = (vc / best_vec) if best_vec > 0 else 0.0
        stored = node["p"]
        out.append(MemoryHit(
            uid=uid, label=node["label"], kind=stored.get("kind"), text=stored.get("text") or "",
            score=KEYWORD_WEIGHT * kw_norm + VECTOR_WEIGHT * vc_norm, keyword_score=kw_norm,
            vector_score=vc_norm, citation=Citation.of(node["label"], stored)))
    out.sort(key=lambda h: (-h.score, h.uid))
    return out[:k]


@runtime_checkable
class MemoryRetriever(Protocol):
    """What agents, Ask, the ExiQoian chat and inline citations read memory through;
    ``MemoryGraph`` implements it (``MemoryGraph.retrieve`` is ``hybrid``)."""

    async def retrieve(self, project_id: str, query: str, *,
                       labels: Sequence[str] = KNOWLEDGE_LABELS, side: str | None = None,
                       phase: str | None = None, k: int = 8, as_of: int | None = None,
                       min_terms: int = 2, vector_floor: float | None = None,
                       include_obsolete: bool = False) -> list[MemoryHit]: ...
