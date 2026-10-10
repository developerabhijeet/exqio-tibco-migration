#!/usr/bin/env python3
"""Regenerate samples/synthetic-gmf, the synthetic GMF-style BusinessWorks estate.

Usage: python scripts/gen_synthetic_gmf.py [output-dir]

Idempotent: the output folder is deleted and rewritten from the definitions in
scripts/synthetic_gmf/ (processes, schemas, resources, database, stubs, estate.json).
Every file is deterministic (xpdlIds and xmi:ids are derived from names), so a
rerun without definition changes produces no diff. Edit the definitions, not the
generated files.
"""
from __future__ import annotations

import sys
from pathlib import Path

from synthetic_gmf.build import build

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "samples" / "synthetic-gmf"


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else DEFAULT_OUT
    count = build(out)
    print(f"wrote {count} files to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
