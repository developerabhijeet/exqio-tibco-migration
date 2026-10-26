"""Line coverage fractions (0..1) from JaCoCo XML, Go cover profiles, and Cobertura/coverage.py XML."""
from __future__ import annotations

from pathlib import Path

from engine.dotnet_gen.coverage import in_scope
from engine.stacks.xmlsafe import MAX_REPORT_BYTES, parse_xml

__all__ = ["parse_cobertura_xml", "parse_go_cover", "parse_jacoco"]


def parse_cobertura_xml(paths: list[Path], scope: set[str] | None = None) -> float | None:
    """coverage.py / Cobertura line coverage over several reports, parsed with the safe XML loader."""
    want = {s.replace("\\", "/").lstrip("./") for s in scope} if scope else None
    covered = valid = 0
    seen = False
    for p in paths:
        for cls in parse_xml(p).iter("class"):
            if want is not None and not in_scope(cls.get("filename") or "", want):
                continue
            seen = True
            for line in cls.iter("line"):
                valid += 1
                covered += (line.get("hits") or "0") != "0"
    if not seen:
        return None
    return covered / valid if valid else 0.0


def parse_jacoco(path: Path, scope: set[str] | None = None) -> float | None:
    """LINE counters. With ``scope`` (source paths) only matching ``sourcefile`` entries count."""
    root = parse_xml(path)
    covered = missed = 0
    if not scope:
        for c in root.findall("counter"):
            if c.get("type") == "LINE":
                covered, missed = int(c.get("covered") or 0), int(c.get("missed") or 0)
    else:
        want = {s.replace("\\", "/").lstrip("./") for s in scope}
        for pkg in root.iter("package"):
            base = (pkg.get("name") or "").strip("/")
            for sf in pkg.findall("sourcefile"):
                full = f"{base}/{sf.get('name')}" if base else (sf.get("name") or "")
                if not in_scope(full, want):
                    continue
                for c in sf.findall("counter"):
                    if c.get("type") == "LINE":
                        covered += int(c.get("covered") or 0)
                        missed += int(c.get("missed") or 0)
    total = covered + missed
    return covered / total if total else None


def parse_go_cover(path: Path, scope: set[str] | None = None) -> float | None:
    """Statement-weighted coverage of a ``-coverprofile``; blocks repeated across packages are merged."""
    p = Path(path)
    if p.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"{p.name} is too large")
    blocks: dict[tuple[str, str], list[int]] = {}
    want = {s.replace("\\", "/").lstrip("./") for s in scope} if scope else None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("mode:"):
            continue
        try:
            loc, stmts, count = line.rsplit(" ", 2)
            file, rng = loc.rsplit(":", 1)
            n, c = int(stmts), int(count)
        except ValueError:
            continue
        if want is not None and not in_scope(file, want):
            continue
        cur = blocks.setdefault((file, rng), [n, 0])
        cur[1] = max(cur[1], c)
    total = sum(n for n, _ in blocks.values())
    hit = sum(n for n, c in blocks.values() if c > 0)
    return hit / total if total else None
