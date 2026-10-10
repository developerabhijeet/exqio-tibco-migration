"""Hybrid search: Postgres full text (``ts_rank_cd`` over a GIN-indexed tsvector) plus pgvector
cosine similarity, each half normalised by its own best score and then combined.

Floors keep irrelevant results out. The keyword half only returns rows that match at least
``min_terms`` of the query's terms (all of them when the query has fewer), so one shared common
word is not a match. The vector half only returns rows whose cosine similarity is at least
``vector_floor``. A query that clears neither floor returns nothing: "capital of France" finds
no migration knowledge and says so.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from . import embeddings
from .embeddings import STOPWORDS

if TYPE_CHECKING:
    from .store import KnowledgeStore

KEYWORD_WEIGHT = 0.5
VECTOR_WEIGHT = 0.5
CANDIDATES_PER_HALF = 50

_COLUMNS = {
    "chunks": ["id", "kind", "process_id", "text"],
    "memory_items": ["id", "kind", "text", "context", "source", "created_by", "created_at"],
}


class Hit(BaseModel):
    id: str
    kind: str
    process_id: str | None = None
    text: str
    score: float


def query_terms(query: str) -> list[str]:
    """Lowercase, de-duplicated search terms with stopwords removed. Identifiers keep their
    underscores (``num_of_pulls``); the database splits them the way it split the documents."""
    seen: dict[str, None] = {}
    for raw in re.findall(r"[A-Za-z0-9_]+", query):
        t = raw.lower().strip("_")
        if t and t not in STOPWORDS:
            seen.setdefault(t, None)
    return list(seen)


def hybrid_rows(
    store: KnowledgeStore,
    table: str,
    project_id: str,
    query: str,
    *,
    k: int = 8,
    vector_floor: float | None = None,
    min_terms: int = 2,
    where: str = "",
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rows of ``table`` (``chunks`` or ``memory_items``) ranked by the combined score; each
    row dict carries ``score``, ``keyword_score`` and ``vector_score`` besides its columns.
    ``where`` is an extra SQL condition on alias ``t`` with named parameters in ``params``."""
    if table not in _COLUMNS:
        raise ValueError(f"unknown table {table!r}")
    if not query.strip() or k <= 0:
        return []
    floor = embeddings.default_vector_floor() if vector_floor is None else vector_floor
    store.check_embedding_backend()
    conn = store.connection()
    cols = ", ".join(f"t.{c}" for c in _COLUMNS[table])
    extra = f" AND ({where})" if where else ""
    base = {"p": project_id, **(params or {})}

    terms = query_terms(query)
    keyword: dict[str, dict[str, Any]] = {}
    if terms:
        required = min(max(min_terms, 1), len(terms))
        rows = conn.execute(
            f"""
            WITH qs AS (
                SELECT plainto_tsquery('english', x) AS q FROM unnest(%(terms)s::text[]) AS x
            ),
            orq AS (
                SELECT string_agg(q::text, ' | ')::tsquery AS q FROM qs WHERE q::text <> ''
            )
            SELECT {cols}, m.matched, m.rank
              FROM knowledge.{table} t
             CROSS JOIN LATERAL (
                   SELECT count(*) FILTER (WHERE t.tsv @@ qs.q) AS matched,
                          coalesce(sum(ts_rank_cd(t.tsv, qs.q)), 0) AS rank
                     FROM qs) m
             WHERE t.project_id = %(p)s{extra}
               AND t.tsv @@ (SELECT q FROM orq) AND m.matched >= %(req)s
             ORDER BY m.rank DESC, t.id LIMIT %(n)s
            """,
            {**base, "terms": terms, "req": required, "n": CANDIDATES_PER_HALF},
        ).fetchall()
        keyword = {r["id"]: r for r in rows}

    qvec = embeddings.embed([query])[0]
    from .store import vector_literal

    vrows = conn.execute(
        f"""
        SELECT {cols}, 1 - (t.embedding <=> %(v)s::vector) AS sim
          FROM knowledge.{table} t
         WHERE t.project_id = %(p)s{extra}
         ORDER BY t.embedding <=> %(v)s::vector LIMIT %(n)s
        """,
        {**base, "v": vector_literal(qvec), "n": CANDIDATES_PER_HALF},
    ).fetchall()
    vector = {r["id"]: r for r in vrows if r["sim"] >= floor}

    best_kw = max((float(r["rank"]) for r in keyword.values()), default=0.0)
    best_vec = max((float(r["sim"]) for r in vector.values()), default=0.0)
    out: list[dict[str, Any]] = []
    for rid in keyword.keys() | vector.keys():
        row = dict(keyword.get(rid) or vector[rid])
        kw = float(keyword[rid]["rank"]) if rid in keyword else 0.0
        vc = float(vector[rid]["sim"]) if rid in vector else 0.0
        kw_norm = (kw / best_kw) if best_kw > 0 else (1.0 if rid in keyword else 0.0)
        vc_norm = (vc / best_vec) if best_vec > 0 else 0.0
        for key in ("matched", "rank", "sim"):
            row.pop(key, None)
        row["keyword_score"] = kw_norm
        row["vector_score"] = vc_norm
        row["score"] = KEYWORD_WEIGHT * kw_norm + VECTOR_WEIGHT * vc_norm
        out.append(row)
    out.sort(key=lambda r: (-r["score"], r["id"]))
    return out[:k]


def hybrid_search(
    store: KnowledgeStore,
    project_id: str,
    query: str,
    k: int = 8,
    vector_floor: float | None = None,
    min_terms: int = 2,
    *,
    kinds: list[str] | None = None,
    process_id: str | None = None,
) -> list[Hit]:
    """Search the analysis chunks of a project. ``vector_floor=None`` uses the active embedding
    backend's calibrated floor (0.66 for bge-small, a measured value for the hash fallback)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if kinds:
        clauses.append("t.kind = ANY(%(kinds)s)")
        params["kinds"] = list(kinds)
    if process_id:
        clauses.append("t.process_id = %(proc)s")
        params["proc"] = process_id
    rows = hybrid_rows(
        store, "chunks", project_id, query, k=k, vector_floor=vector_floor,
        min_terms=min_terms, where=" AND ".join(clauses), params=params,
    )
    return [
        Hit(id=r["id"], kind=r["kind"], process_id=r["process_id"], text=r["text"],
            score=r["score"])
        for r in rows
    ]
