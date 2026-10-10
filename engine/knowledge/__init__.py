"""Knowledge graph, hybrid search and migration memory (Postgres + pgvector, schema ``knowledge``).

Import the submodules directly. Nothing is imported here so that ``engine.agents.provider`` can
import ``MemoryItem`` from ``engine.knowledge.memory`` without pulling in the store.
"""
