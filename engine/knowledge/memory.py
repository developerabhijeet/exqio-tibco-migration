"""Migration memory: SME decisions, mapping choices and RSI seed patterns that grow with every
migration and are retrieved by hybrid search.

``MemoryItem`` is defined here and imported by ``engine.agents.provider`` (this module must not
import the store at module level, or that import would become circular).

Honesty note: the seed patterns in ``seed_patterns.yaml`` are TIBCO-to-.NET patterns authored
by R Systems (``source: rsi-seed``). They are curated guidance, retrieved by search. Nothing in
this module trains or fine-tunes a model, and nothing here should be described as such.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, Field

from . import embeddings, search

if TYPE_CHECKING:
    from .store import KnowledgeStore

MemoryKind = Literal["decision", "pattern", "mapping", "note", "seed"]
MEMORY_KINDS: tuple[str, ...] = ("decision", "pattern", "mapping", "note", "seed")
SEED_FILE = Path(__file__).with_name("seed_patterns.yaml")
SEED_SOURCE = "rsi-seed"


class MemoryItem(BaseModel):
    id: str
    project_id: str = ""
    kind: MemoryKind
    text: str
    context: dict[str, Any] = Field(default_factory=dict)
    source: str
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    score: float | None = None


def load_seed_patterns(path: Path = SEED_FILE) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    patterns = data.get("patterns", [])
    for p in patterns:
        for key in ("id", "title", "when", "then", "source", "kind"):
            if not p.get(key):
                raise ValueError(f"seed pattern {p.get('id', '?')!r} is missing {key!r}")
    return patterns


def pattern_text(p: dict[str, Any]) -> str:
    return f"{p['title']}. When: {p['when'].strip()} Then: {p['then'].strip()}"


class MigrationMemory:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def record(
        self,
        project_id: str,
        kind: str,
        text: str,
        context: dict[str, Any] | None,
        source: str,
        created_by: str,
        *,
        item_id: str | None = None,
    ) -> str:
        """Store one memory item and return its id. Raw TIBCO XML is refused."""
        from psycopg.types.json import Jsonb

        from .store import KnowledgeError, guard_text, vector_literal

        if kind not in MEMORY_KINDS:
            raise KnowledgeError(f"unknown memory kind {kind!r}")
        guard_text(text)
        self.store.check_embedding_backend()
        mid = item_id or f"mem_{uuid.uuid4().hex[:12]}"
        vec = embeddings.embed([text])[0]
        row = self.store.connection().execute(
            "INSERT INTO knowledge.memory_items (id, project_id, kind, text, context, source, "
            "created_by, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector) "
            "ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, text = EXCLUDED.text, "
            "context = EXCLUDED.context, source = EXCLUDED.source, "
            "embedding = EXCLUDED.embedding "
            "WHERE knowledge.memory_items.project_id = EXCLUDED.project_id RETURNING id",
            [mid, project_id, kind, text, Jsonb(context or {}), source, created_by,
             vector_literal(vec)],
        ).fetchone()
        if row is None:  # the id exists in another project: never overwrite it
            raise KnowledgeError(f"memory id {mid!r} already belongs to another project")
        return mid

    def retrieve(
        self,
        project_id: str,
        query: str,
        kinds: list[str] | None = None,
        k: int = 6,
        *,
        vector_floor: float | None = None,
        min_terms: int = 2,
    ) -> list[MemoryItem]:
        where = "t.kind = ANY(%(kinds)s)" if kinds else ""
        rows = search.hybrid_rows(
            self.store, "memory_items", project_id, query, k=k, vector_floor=vector_floor,
            min_terms=min_terms, where=where, params={"kinds": list(kinds)} if kinds else None,
        )
        return [
            MemoryItem(
                id=r["id"], project_id=project_id, kind=r["kind"], text=r["text"],
                context=r["context"], source=r["source"], created_by=r["created_by"],
                created_at=r["created_at"], score=r["score"],
            )
            for r in rows
        ]

    def seed_rsi_patterns(self, project_id: str) -> list[str]:
        """Load the R Systems seed patterns into a project. Idempotent: ids are
        ``seed:<project>:<pattern id>``, so a second call updates rather than duplicates."""
        ids: list[str] = []
        for p in load_seed_patterns():
            context = {
                "title": p["title"], "when": p["when"].strip(), "then": p["then"].strip(),
                "tibco": p.get("tibco", ""), "dotnet": p.get("dotnet", ""),
                "authored_by": "R Systems",
            }
            ids.append(self.record(
                project_id, p["kind"], pattern_text(p), context, p["source"], "rsi",
                item_id=f"seed:{project_id}:{p['id']}",
            ))
        return ids
