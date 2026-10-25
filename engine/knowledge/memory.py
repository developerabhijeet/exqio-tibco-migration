"""Migration memory as the agents see it: SME decisions, mapping choices and seed patterns,
stored as ``Memory`` nodes of the Neo4j memory graph and retrieved by its hybrid search
(``api.app.services.knowledge.Knowledge.retrieve_memory`` builds the items).

``MemoryItem`` is defined here and imported by ``engine.agents.provider``, so this module imports
nothing of the memory graph.

Honesty note: the seed patterns in ``seed_patterns.yaml`` are TIBCO-to-.NET patterns shipped
with the platform (``source: seed``). They are curated guidance, retrieved by search. Nothing in
this module trains or fine-tunes a model, and nothing here should be described as such.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

MemoryKind = Literal["decision", "pattern", "mapping", "note", "seed"]
MEMORY_KINDS: tuple[str, ...] = ("decision", "pattern", "mapping", "note", "seed")
SEED_FILE = Path(__file__).with_name("seed_patterns.yaml")
SEED_SOURCE = "seed"


class MemoryItem(BaseModel):
    id: str
    project_id: str = ""
    kind: MemoryKind
    text: str
    context: dict[str, Any] = Field(default_factory=dict)
    source: str
    created_by: str = ""
    created_at: datetime | None = None  # seed patterns carry none (rulings R28)
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
