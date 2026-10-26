"""Parse non-.NET test results into ``(name, outcome, duration_ms, message)`` tuples."""
from __future__ import annotations

import json
from pathlib import Path

from engine.dotnet_gen.trx import Outcome
from engine.stacks.xmlsafe import MAX_REPORT_BYTES, parse_xml

Case = tuple[str, Outcome, float, str]


def parse_junit(paths: list[Path]) -> list[Case]:
    """Surefire and pytest JUnit XML: ``classname.name``; failure/error -> failed, skipped -> skipped."""
    out: list[Case] = []
    for p in paths:
        root = parse_xml(p)
        for tc in root.iter("testcase"):
            cls, name = tc.get("classname") or "", tc.get("name") or ""
            full = f"{cls}.{name}" if cls else name
            outcome: Outcome = "passed"
            message = ""
            for child in tc:
                tag = child.tag if isinstance(child.tag, str) else ""
                if tag in ("failure", "error"):
                    outcome = "failed"
                    message = (child.get("message") or (child.text or "")).strip()
                    break
                if tag == "skipped":
                    outcome = "skipped"
                    message = (child.get("message") or (child.text or "")).strip()
            try:
                ms = round(float(tc.get("time") or 0) * 1000.0, 6)
            except ValueError:
                ms = 0.0
            out.append((full, outcome, ms, message))
    return out


def parse_test2json(path: Path) -> list[Case]:
    """``go test -json``: one case per top-level ``Package.Test`` with a pass/fail/skip event."""
    p = Path(path)
    if p.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"{p.name} is too large")
    output: dict[str, list[str]] = {}
    out: list[Case] = []
    with p.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue  # build output interleaved with the JSON stream
            test = ev.get("Test")
            if not isinstance(ev, dict) or not test or "/" in test:
                continue
            key = f"{ev.get('Package', '')}.{test}"
            action = ev.get("Action")
            if action == "output":
                output.setdefault(key, []).append(ev.get("Output", ""))
            elif action in ("pass", "fail", "skip"):
                outcome: Outcome = {"pass": "passed", "fail": "failed", "skip": "skipped"}[action]
                msg = "" if outcome == "passed" else "".join(output.get(key, [])).strip()
                out.append((key, outcome, round(float(ev.get("Elapsed") or 0) * 1000.0, 6), msg))
    return out
