"""Closed sets of the memory graph: node labels, relationship types and the embedded labels."""
from __future__ import annotations

NODE_LABELS: tuple[str, ...] = (
    "Application", "Process", "Activity", "Contract", "SharedResource", "SubstVar", "Table",
    "Queue", "Topic", "Endpoint", "Domain", "Solution", "CodeProject", "CodeFile", "Class",
    "Method", "ConfigKey", "MessagingEntity", "Test", "Chunk", "Memory", "GateDecision", "Error",
    "Curation",
)

REL_TYPES: tuple[str, ...] = (
    "CONTAINS", "CALLS", "READS", "WRITES", "PUBLISHES", "CONSUMES", "EXPOSES", "USES",
    "MIGRATED_TO", "TESTS", "FAILED_AT", "ABOUT",
)

# Labels that carry a 384-dimension `embedding` and have a vector index.
EMBEDDED_LABELS: tuple[str, ...] = ("Chunk", "Memory", "Process", "Activity", "Class", "Method")
