"""Expert rules: CSV parsing and validation, and the prompt section each phase receives."""
from __future__ import annotations

from pathlib import Path

from engine.agents import expert_rules as er
from engine.agents.prompting import render_prompt

CSV = Path(__file__).resolve().parents[2] / "samples" / "rules" / "tibco-expert-rules.csv"
HEADER = ",".join(er.COLUMNS)


def _csv(*rows: str, header: str = HEADER) -> bytes:
    return ("\r\n".join([header, *rows]) + "\r\n").encode()


def test_real_csv_imports_clean():
    rep = er.parse_csv(CSV.read_bytes())
    assert rep.fatal is None and rep.errors == []
    assert rep.total == 125 and len(rep.rows) == 125
    first = rep.rows[0]
    assert first["rule_id"] == "TIB-001" and first["severity"] == "Critical"
    assert first["primary_agent"] == "Discovery Agent"
    assert all("�" not in r["guidance"] for r in rep.rows)  # cp1252 dashes decoded


def test_bad_header_is_fatal():
    rep = er.parse_csv(b"Rule,Domain,Severity\r\nTIB-001,x,High\r\n")
    assert rep.fatal and "Rule ID" in rep.fatal and rep.rows == []


def test_header_matched_case_and_space_insensitive():
    header = " rule id ,SEVERITY,best practice/rule name,Codified Rule /  Expert Guidance"
    rep = er.parse_csv(_csv("TIB-1,high,Name,Do it", header=header))
    assert rep.fatal is None and rep.rows[0]["severity"] == "High"
    assert any(w["field"] == "Domain" for w in rep.warnings)


def _row(rid: str = "TIB-001", sev: str = "High", name: str = "Name", guidance: str = "Guide") -> str:
    cells = [rid, "D", name, sev, guidance] + [""] * 10
    return ",".join(cells)


def test_duplicate_bad_id_and_bad_severity_rows_are_skipped():
    rep = er.parse_csv(_csv(_row(), _row(), _row("tib-2"), _row("TIB-3", sev="Urgent"), _row("TIB-4", name=""),
                            _row("TIB-5", sev="low")))
    assert [r["rule_id"] for r in rep.rows] == ["TIB-001", "TIB-5"]
    assert rep.rows[1]["severity"] == "Low"
    by_row = {(e["row"], e["field"]) for e in rep.errors}
    assert by_row == {(3, "rule_id"), (4, "rule_id"), (5, "severity"), (6, "name")}
    assert "duplicate" in next(e["message"] for e in rep.errors if e["row"] == 3)


def test_quoted_multiline_cells_and_lf_only():
    text = HEADER + '\nTIB-9,D,"Two\nline name",Medium,"Guide, with comma\nand ""quotes"""' + ",," * 5 + "\n"
    rep = er.parse_csv(text.encode("utf-8-sig"))
    assert rep.errors == [] and rep.rows[0]["name"] == "Two\nline name"
    assert rep.rows[0]["guidance"] == 'Guide, with comma\nand "quotes"'


def test_too_large_and_empty():
    assert er.parse_csv(b"x" * (er.MAX_BYTES + 1)).fatal
    assert er.parse_csv(b"").fatal


def test_template_parses():
    rep = er.parse_csv(er.template_csv().encode())
    assert rep.errors == [] and len(rep.rows) == 1


def test_phases_for_agents():
    assert er.phases_for("Discovery Agent") == ("discover", "analysis")
    assert er.phases_for("TIBCO Expert Agent") == ("analysis", "design")
    assert er.phases_for("Architecture Agent") == ("design",)
    assert er.phases_for("Transformation Agent") == ("build",)
    assert "verify" in er.phases_for("Validation Agent")
    assert er.phases_for("Operations Agent") == ("deploy",)
    assert er.phases_for("Someone") == er.PHASES


def test_section_orders_by_severity_and_caps():
    rules = [{"rule_id": f"TIB-{i}", "severity": s, "name": "n", "guidance": "g" * 200,
              "primary_agent": "Architecture Agent", "enabled": True}
             for i, s in enumerate(["Low", "Critical", "High"] * 20)]
    text, ids = er.section(rules, "design", limit=1000)
    assert text.startswith("## " + er.SECTION_TITLE)
    assert ids[0] == "TIB-1" and len(ids) < 60 and "left out for length" in text
    assert er.section(rules, "build") == ("", [])


def test_prompt_carries_section_only_with_rules():
    plain, _ = render_prompt("analyst", source="x", process_name="p", memory="", rules="", findings="")
    assert er.SECTION_TITLE not in plain
    active = er.ActiveRules([{"rule_id": "TIB-001", "severity": "Critical", "name": "Inventory",
                              "guidance": "Find every asset", "pass_criteria": "All owned",
                              "enforcement": "Block", "primary_agent": "Discovery Agent", "enabled": True}])
    token = er.ACTIVE.set(active)
    try:
        system, _ = render_prompt("analyst", source="x", process_name="p", memory="", rules="", findings="")
        designer, _ = render_prompt("designer", source="x", process_name="p")
    finally:
        er.ACTIVE.reset(token)
    assert system.startswith(plain) and er.SECTION_TITLE in system and "TIB-001 [Critical]" in system
    assert er.SECTION_TITLE not in designer  # a Discovery rule is not a design rule
    assert active.used == {"analysis": {"TIB-001"}}
