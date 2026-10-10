"""Cobertura (coverlet ``XPlat Code Coverage``) parsing: line coverage as a fraction 0..1.

coverlet instruments the assemblies the test project references (the generated Api, Core and
Infrastructure projects), not the test assembly itself, so the root ``line-rate`` is the line
coverage of the generated service projects (design spec 2.5).
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree


def parse_cobertura(path: str | Path) -> float:
    """Root ``line-rate``; when absent, ``lines-covered / lines-valid`` (0.0 when nothing is valid)."""
    root = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
    rate = root.get("line-rate")
    if rate not in (None, ""):
        return float(rate)
    covered = float(root.get("lines-covered") or 0)
    valid = float(root.get("lines-valid") or 0)
    return covered / valid if valid else 0.0


def merge_cobertura(paths: list[str | Path]) -> float | None:
    """Line coverage over several reports (one per test project): total covered / total valid."""
    covered = valid = 0.0
    for p in paths:
        root = etree.parse(str(p), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
        c, v = root.get("lines-covered"), root.get("lines-valid")
        if c is None or v is None:
            return parse_cobertura(paths[0]) if len(paths) == 1 else None
        covered += float(c)
        valid += float(v)
    if not paths:
        return None
    return covered / valid if valid else 0.0
