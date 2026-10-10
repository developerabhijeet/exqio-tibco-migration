"""Spec hash, gates G1 to G6, their enablement rules and the audit trail (design spec 5.2).

Spec hash: ``spec_hash(ir, approved_rules, finding_decisions)`` where the approved rules are
the rules with status ``approved`` or ``edited`` (``{id: rule key, text, citations}``) and the
decisions are the decided findings (``{id: finding key, decision, note}``).

Resets:

* a rule decision or edit moves the rules part of the hash: G1 to G6 reset;
* a finding decision moves the decisions part: G2 to G6 reset, and a decided G1 is rebound to
  the new hash (G1 covers the rules only), with an audit line ``gate rebound``;
* an analysis re-run resets G1 to G6 when the hash or the set of rules or findings changed;
* anything else that moved the hash (a rescan that changed the IR) is caught on the next read
  by :func:`reconcile`: every decided gate whose recorded hash differs is reset.

Every reset writes an audit line ``gate reset`` with the gate, the reason and both hashes.
Gate decisions refuse agents (``require_user`` in the router) and are refused with 409 and the
reason when a gate is not enabled. G3 to G6 are enabled when the previous gate is approved;
Task 19 adds their own evidence checks.
"""
from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db import utcnow
from api.app.models import AnalysisRun, AuditLog, CoverageItem, Finding, Gate, Process, Rule
from engine.ir.spec_hash import spec_hash

GATES: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6")
RULE_DECIDED = frozenset({"approved", "edited", "rejected"})
RULE_APPROVED = frozenset({"approved", "edited"})
SYSTEM = "system"
STALE_REASON = "spec hash changed since the gate was decided"


class GateBlocked(Exception):
    """The gate is not enabled; the message is the reason (HTTP 409)."""


def key_number(key: str) -> int:
    m = re.search(r"(\d+)$", key or "")
    return int(m.group(1)) if m else 0


def rule_order(r: Rule) -> tuple[int, str]:
    return key_number(r.rule_key), r.rule_key


def finding_order(f: Finding) -> tuple[int, str, int]:
    return (0 if f.source == "static" else 1), (f.finding_key if f.source == "static" else ""), \
        key_number(f.finding_key)


async def load_rules(session: AsyncSession, process_id: str) -> list[Rule]:
    rows = (await session.execute(select(Rule).where(Rule.process_id == process_id))).scalars().all()
    return sorted(rows, key=rule_order)


async def load_findings(session: AsyncSession, process_id: str) -> list[Finding]:
    rows = (await session.execute(select(Finding).where(Finding.process_id == process_id))).scalars().all()
    return sorted(rows, key=finding_order)


async def latest_analysis(session: AsyncSession, process_id: str) -> AnalysisRun | None:
    return await session.scalar(select(AnalysisRun).where(AnalysisRun.process_id == process_id)
                                .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id).limit(1))


def hash_rules(rules: list[Rule]) -> list[dict[str, Any]]:
    return [{"id": r.rule_key, "text": r.text, "citations": r.citations or []}
            for r in rules if r.status in RULE_APPROVED]


def hash_decisions(findings: list[Finding]) -> list[dict[str, Any]]:
    return [{"id": f.finding_key, "decision": f.decision, "note": f.note or ""}
            for f in findings if f.decision]


async def current_spec_hash(session: AsyncSession, process_id: str, ir: dict[str, Any]) -> str:
    rules = await load_rules(session, process_id)
    findings = await load_findings(session, process_id)
    return spec_hash(ir, hash_rules(rules), hash_decisions(findings))


def audit(session: AsyncSession, process: Process | None, actor: str, action: str, *,
          gate: str | None = None, detail: dict[str, Any] | None = None,
          actor_kind: str = "user", project_id: str | None = None) -> None:
    session.add(AuditLog(project_id=project_id or (process.project_id if process else None),
                         process_id=process.id if process else None, actor=actor,
                         actor_kind=actor_kind, action=action, gate=gate, detail=detail or {}))


async def gate_rows(session: AsyncSession, process_id: str) -> dict[str, Gate]:
    rows = (await session.execute(select(Gate).where(Gate.process_id == process_id))).scalars().all()
    return {g.gate: g for g in rows}


async def reset_gates(session: AsyncSession, process: Process, rows: dict[str, Gate], from_gate: str,
                      reason: str, actor: str, actor_kind: str,
                      detail: dict[str, Any] | None = None) -> list[str]:
    """Set every decided gate from ``from_gate`` on back to pending, with one audit line each."""
    reset: list[str] = []
    for name in GATES[GATES.index(from_gate):]:
        row = rows.get(name)
        if row is None or row.state == "pending":
            continue
        previous = row.state
        row.state = "pending"
        row.reset_reason = reason
        row.decided_by = row.decided_at = row.note = None
        audit(session, process, actor, "gate reset", gate=name, actor_kind=actor_kind,
              detail={"reason": reason, "previous_state": previous, **(detail or {})})
        reset.append(name)
    if "G2" in reset and process.migration_status == "validated":
        process.migration_status = "analysed"
    return reset


async def apply_spec_hash(session: AsyncSession, process: Process, *, reason: str, actor: str,
                          actor_kind: str = "user", reset_from: str = "G1",
                          force: bool = False) -> tuple[str, str, list[str]]:
    """Recompute the spec hash after a change; reset gates from ``reset_from`` when it moved
    (or when ``force``), and rebind decided gates before ``reset_from`` to the new hash."""
    await session.flush()
    old = process.spec_hash
    new = await current_spec_hash(session, process.id, process.ir)
    reset: list[str] = []
    if new == old and not force:
        return old, new, reset
    process.spec_hash = new
    rows = await gate_rows(session, process.id)
    hashes = {"old_spec_hash": old, "new_spec_hash": new}
    reset = await reset_gates(session, process, rows, reset_from, reason, actor, actor_kind, hashes)
    for name in GATES[:GATES.index(reset_from)]:
        row = rows.get(name)
        if row is not None and row.state != "pending" and row.spec_hash != new:
            audit(session, process, actor, "gate rebound", gate=name, actor_kind=actor_kind,
                  detail={"reason": reason, "old_spec_hash": row.spec_hash, "new_spec_hash": new})
            row.spec_hash = new
    return old, new, reset


async def reconcile(session: AsyncSession, process: Process) -> list[str]:
    """Reset decided gates whose recorded spec hash is not the process's (a rescan moved it)."""
    rows = await gate_rows(session, process.id)
    if not any(r.state != "pending" and r.spec_hash != process.spec_hash for r in rows.values()):
        return []
    return await reset_gates(session, process, rows, "G1", STALE_REASON, SYSTEM, SYSTEM,
                             {"new_spec_hash": process.spec_hash})


# ------------------------------------------------------------------ enablement
def unknown_activity_blockers(ir: dict[str, Any]) -> list[str]:
    unknown = sorted({a.get("native_type") or a.get("id", "?") for a in ir.get("activities") or []
                      if a.get("type") == "unknown"})
    return [f"unknown activity types: {', '.join(unknown)}"] if unknown else []


async def gate_reasons(session: AsyncSession, process: Process) -> dict[str, list[str]]:
    """Why each gate is not enabled (an empty list means enabled)."""
    rows = await gate_rows(session, process.id)
    analysis = await latest_analysis(session, process.id)
    out: dict[str, list[str]] = {}

    g1: list[str] = []
    g2: list[str] = []
    if analysis is None:
        g1.append("analysis has not run")
        g2.append("analysis has not run")
    g1 += unknown_activity_blockers(process.ir)
    if analysis is not None:
        rules = await load_rules(session, process.id)
        undecided = [r.rule_key for r in rules if r.status not in RULE_DECIDED]
        if undecided:
            g1.append(f"{len(undecided)} rule(s) undecided: {', '.join(undecided)}")
        status = {r.rule_key: r.status for r in rules}
        ver = analysis.verification or {}
        live = [f for f in ver.get("failures") or [] if status.get(f.get("rule_id")) != "rejected"]
        if live:
            keys = sorted({f.get("rule_id") for f in live}, key=key_number)
            g1.append(f"citations verified {ver.get('verified', 0)} of {ver.get('total', 0)}: "
                      f"failing on {', '.join(keys)}")
        items = (await session.execute(select(CoverageItem)
                                       .where(CoverageItem.process_id == process.id))).scalars().all()
        uncovered = [i.ref for i in items if not i.covered_by and not i.technical_reason]
        if uncovered:
            shown = ", ".join(uncovered[:5]) + (" ..." if len(uncovered) > 5 else "")
            g1.append(f"coverage {len(items) - len(uncovered)} of {len(items)}: uncovered {shown}")
        findings = await load_findings(session, process.id)
        open_findings = [f.finding_key for f in findings if not f.decision]
        if open_findings:
            g2.append(f"{len(open_findings)} finding(s) undecided: {', '.join(open_findings)}")
    out["G1"] = g1
    if (g := rows.get("G1")) is None or g.state != "approved":
        g2.append("G1 not approved")
    out["G2"] = g2
    for prev, name in pairwise(GATES[1:]):
        row = rows.get(prev)
        out[name] = [] if row is not None and row.state == "approved" else [f"{prev} not approved"]
    return out


def gate_out(name: str, row: Gate | None, reasons: list[str]) -> dict[str, Any]:
    return {
        "gate": name,
        "state": row.state if row else "pending",
        "enabled": not reasons,
        "reason": "; ".join(reasons) or None,
        "spec_hash": row.spec_hash if row else None,
        "design_hash": row.design_hash if row else None,
        "build_hash": row.build_hash if row else None,
        "evidence_hash": row.evidence_hash if row else None,
        "decided_by": row.decided_by if row else None,
        "decided_at": row.decided_at.isoformat() if row and row.decided_at else None,
        "note": row.note if row else None,
        "reset_reason": row.reset_reason if row else None,
    }


async def gates_view(session: AsyncSession, process: Process) -> list[dict[str, Any]]:
    rows = await gate_rows(session, process.id)
    reasons = await gate_reasons(session, process)
    return [gate_out(name, rows.get(name), reasons[name]) for name in GATES]


async def decide(session: AsyncSession, process: Process, gate: str, state: str, note: str | None,
                 user: str) -> dict[str, Any]:
    """Approve or reject one gate; 409 (``GateBlocked``) when it is not enabled."""
    await reconcile(session, process)
    reasons = (await gate_reasons(session, process))[gate]
    if reasons:
        raise GateBlocked(f"{gate} is not enabled: {'; '.join(reasons)}")
    rows = await gate_rows(session, process.id)
    row = rows.get(gate)
    if row is None:
        row = Gate(process_id=process.id, gate=gate)
        session.add(row)
        rows[gate] = row
    previous = row.state
    row.state = state
    row.spec_hash = process.spec_hash
    row.decided_by = user
    row.decided_at = utcnow()
    row.note = note
    row.reset_reason = None
    audit(session, process, user, f"gate {state}", gate=gate,
          detail={"spec_hash": process.spec_hash, "note": note, "previous_state": previous})
    if state == "rejected" and gate != GATES[-1]:
        await reset_gates(session, process, rows, GATES[GATES.index(gate) + 1], f"{gate} rejected",
                          user, "user", {"spec_hash": process.spec_hash})
    if gate == "G2":
        if state == "approved":
            process.migration_status = "validated"
        elif process.migration_status == "validated":
            process.migration_status = "analysed"
    await session.flush()
    return gate_out(gate, row, [])
