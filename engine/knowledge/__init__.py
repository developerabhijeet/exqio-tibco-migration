"""Embeddings, the seed patterns, the migration memory item model and Ask. The knowledge itself
lives in the Neo4j memory graph (``engine.memory``).

Import the submodules directly. Nothing is imported here so that ``engine.agents.provider`` can
import ``MemoryItem`` from ``engine.knowledge.memory`` without pulling in anything else.
"""
