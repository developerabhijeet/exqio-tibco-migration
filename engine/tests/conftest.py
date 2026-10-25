"""Shared pytest configuration for the engine test-suite."""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


SKIP_NO_NEO4J = ("NEO4J_TEST_URI is not set: run make test (starts neo4j-test) or point it at a "
                 "Neo4j 5.26")


@pytest.fixture
async def memory_graph():
    """A MemoryGraph on the test Neo4j with the schema and no data; skipped when it is unset."""
    uri = os.environ.get("NEO4J_TEST_URI", "")  # an empty value counts as unset
    if not uri:
        pytest.skip(SKIP_NO_NEO4J)
    from engine.memory.graph import MemoryGraph
    graph = MemoryGraph.connect(uri, os.environ.get("NEO4J_TEST_USER", "neo4j"),
                                os.environ.get("NEO4J_TEST_PASSWORD", "exiqo-test-neo4j"))
    try:
        await graph.ensure_schema()
        await graph.reset()
        yield graph
    finally:
        await graph.close()
