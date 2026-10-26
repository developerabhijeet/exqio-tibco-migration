"""Fail fast when sub-project 2 (memory) or 3 (citation engine and web components) is missing."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_sp3_citation_model_exists():
    from engine.citations.model import CitedText, CiteRef, CiteSpan, CiteTarget  # noqa: F401
    from engine.citations.spans import sentence_bounds
    assert sentence_bounds("A. B.") == [(0, 2), (3, 5)]
    with pytest.raises(ValueError):
        CitedText(text="ab", spans=[CiteSpan(start=0, end=3, refs=[])], refs=[])


def test_sp2_projectors_and_routes_exist():
    from api.app.routers.memory import router
    from engine.memory.projectors.analysis import project_analysis  # noqa: F401
    from engine.memory.projectors.build import project_build  # noqa: F401
    from engine.memory.projectors.design import project_design  # noqa: F401
    assert "/memory/{project_id}/node" in {r.path for r in router.routes}


def test_web_prerequisites_exist():
    # ExiQoianPanel (SP3 Task 9) is deliberately not required (cross-ruling X5).
    for p in ("web/lib/citations.ts", "web/components/citations/CitationChip.tsx",
              "web/components/citations/CitationPreview.tsx", "web/components/citations/CitedText.tsx",
              "web/app/memory/page.tsx"):
        assert (REPO_ROOT / p).exists(), f"missing prerequisite {p} (sub-project 2 or 3 not done)"
    src = (REPO_ROOT / "web/lib/citations.ts").read_text()
    assert "useCiteNavigate" in src and "sliceCodePoints" in src
