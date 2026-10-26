"""Fail fast when a sub-project 2 (Neo4j memory) interface ExiQoian builds on is missing."""
import inspect
from pathlib import Path


def test_memory_models_and_retriever_exist():
    from engine.memory.graph import MemoryGraph
    from engine.memory.model import KNOWLEDGE_LABELS, NODE_LABELS, Citation, MemoryHit
    from engine.memory.search import MemoryRetriever
    assert {"uid", "label", "phase", "revision", "artifact_type", "artifact_id", "process_id", "path"} <= set(Citation.model_fields)
    assert {"uid", "label", "kind", "text", "score", "citation"} <= set(MemoryHit.model_fields)
    params = inspect.signature(MemoryRetriever.retrieve).parameters
    assert {"labels", "phase", "k", "min_terms", "vector_floor", "include_obsolete"} <= set(params)
    assert callable(getattr(MemoryGraph, "retrieve", None)), "SP2: MemoryGraph.retrieve missing"
    assert set(KNOWLEDGE_LABELS) == {"Chunk", "Memory"} and {"GateDecision", "Error", "Curation", "Class", "Method"} <= set(NODE_LABELS)


def test_knowledge_facade_and_memory_routes_exist(app):
    from api.app.services.knowledge import Knowledge, KnowledgeUnavailable  # noqa: F401
    assert hasattr(Knowledge, "enabled")
    paths = set(app.openapi()["paths"])  # app.routes holds _IncludedRouter objects without .path
    assert "/memory/{project_id}/node" in paths, "SP2 Task 12 (/memory read API) not done"


def test_viewer_persona_and_alembic_head():
    from api.app.auth.roles import DEV_PERSONAS
    assert DEV_PERSONAS.get("viewer.val") == frozenset({"viewer"}), "SP2 Task 13 (viewer.val) not done"
    assert Path("api/alembic/versions/0007_memory_events_job_retry.py").exists()


def test_memory_explorer_page_exists():
    assert Path("web/app/memory/page.tsx").exists(), "SP2 Task 14 (/memory explorer) not done"
