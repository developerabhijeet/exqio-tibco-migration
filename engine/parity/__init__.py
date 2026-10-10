"""Parity evaluator: expected output derived from the TIBCO model (design spec section 6)."""
from engine.parity.compare import LineageEntry, NoiseRule, NumericRule, build_lineage, compare
from engine.parity.evaluator import EvaluationError, Evaluator, UnsupportedActivity
from engine.parity.scenarios import fill_expected, generate_and_evaluate, generate_scenarios
from engine.parity.schema import SchemaIndex
from engine.parity.seed import SeedData
from engine.parity.stubs import WireMockStubs

__all__ = [
    "EvaluationError",
    "Evaluator",
    "LineageEntry",
    "NoiseRule",
    "NumericRule",
    "SchemaIndex",
    "SeedData",
    "UnsupportedActivity",
    "WireMockStubs",
    "build_lineage",
    "compare",
    "fill_expected",
    "generate_and_evaluate",
    "generate_scenarios",
]
