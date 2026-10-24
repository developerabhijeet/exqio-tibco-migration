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


def merge_cobertura(paths: list[str | Path], scope: set[str] | None = None) -> float | None:
    """Line coverage over several reports (one per test project): total covered / total valid.

    With ``scope`` - the source files generated for the process being built - only classes from
    those files are counted. The solution the tests run against is larger than the migration: the
    Service Bus adapter the test host replaces with an in-memory bus, the TIBCO value helpers, the
    masking and messaging types shared by every process. None of that is reachable from a test of
    this workflow, so counting it answers "how much of the whole scaffold did these tests touch"
    when the question G4 asks is "did they exercise the code this workflow became"."""
    if scope:
        return _scoped(paths, scope)
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


def in_scope(filename: str, scope: set[str]) -> bool:
    """Whether a cobertura ``filename`` is one of ``scope``.

    coverlet writes the path relative to the project directory ("GmfCredit.Core/Services/X.cs")
    and the generator names files relative to the solution ("src/GmfCredit.Core/Services/X.cs"),
    so the comparison is on the longest common tail."""
    name = filename.replace("\\", "/").lstrip("./")
    return any(name.endswith(s) or s.endswith(name) for s in scope)


def _scoped(paths: list[str | Path], scope: set[str]) -> float | None:
    """Covered over valid lines of the classes whose file is in ``scope``; ``None`` when none is."""
    want = {s.replace("\\", "/").lstrip("./") for s in scope}
    covered = valid = 0
    seen = False
    for p in paths:
        root = etree.parse(str(p), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
        for cls in root.iter("class"):
            if not in_scope(cls.get("filename") or "", want):
                continue
            seen = True
            for line in cls.iter("line"):
                valid += 1
                if (line.get("hits") or "0") != "0":
                    covered += 1
    if not seen:
        return None
    return covered / valid if valid else 0.0
