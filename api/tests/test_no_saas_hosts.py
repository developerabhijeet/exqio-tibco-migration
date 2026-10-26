"""No public SaaS host is written into the platform's code: connector hosts come from the chart's
values (GMF-controlled), never from a string in api/app or engine."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = [re.compile(p) for p in (r"api\.github\.com", r"atlassian\.net", r"api\.figma\.com", r"www\.figma\.com")]
SUFFIXES = {".py", ".j2", ".md", ".json", ".yaml", ".yml", ".txt", ".sql", ".toml"}


def grep_strings(roots: list[str], root: Path = ROOT) -> list[str]:
    hits = []
    for r in roots:
        for path in sorted((root / r).rglob("*")):
            if not path.is_file() or path.suffix not in SUFFIXES or "tests" in path.parts or "__pycache__" in path.parts:
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if any(p.search(line) for p in PATTERNS):
                    hits.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    return hits


def test_no_public_saas_host_in_code():
    assert grep_strings(["api/app", "engine"]) == []


def test_the_scan_sees_a_planted_host(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.py").write_text('URL = "https://api.github.com/user"\n')
    assert len(grep_strings(["x"], tmp_path)) == 1
