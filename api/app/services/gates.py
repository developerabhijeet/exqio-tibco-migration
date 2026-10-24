"""Spec hash, gates G1 to G6, their enablement rules and the audit trail (design spec 5.2).

Spec hash: ``spec_hash(ir, approved_rules, finding_decisions)`` where the approved rules are
the rules with status ``approved`` or ``edited`` (``{id: rule key, text, citations}``) and the
decisions are the decided findings (``{id: finding key, decision, note}``).

Resets:

* a rule decision or edit moves the rules part of the hash: G1 to G6 reset;
* a finding decision moves the decisions part: G2 to G6 reset, and a decided G1 is rebound to
  the new hash (G1 covers the rules only), with an audit line ``gate rebound``;
* an analysis re-run resets G1 to G6 when the hash or the set of rules or findings changed;
* anything else that moved the hash (a rescan that changed the IR) is caught by
  :func:`reconcile` on the next action (a rule or finding decision, an analysis, a gate
  decision): every decided gate whose recorded hash differs is reset. Reads never write: until
  then the gate shows ``stale: true`` and a stale approval does not enable the next gate.

G1 also needs the latest analysis to have seen the current IR (``ir_hash`` of the IR the job
analyzed, stored on the run); after a rescan that changed the IR the reason is "IR changed since
analysis: re-run analysis". A partial analysis (stored with ``PARTIAL_NOTICE``: a model stage
failed) keeps G1 disabled with "analysis is partial (<stages> failed); re-run analysis before
approving G1" until a complete run is stored.
A gate may always be rejected by its owner, enabled or not; the note is recorded as the reason.

Every reset writes an audit line ``gate reset`` with the gate, the reason and both hashes.
Gate decisions refuse agents (``require_user`` in the router) and are refused with 409 and the
reason when a gate is not enabled. G3 to G6 are enabled when the previous gate is approved and
their evidence exists (:mod:`api.app.services.evidence`); a decision on them also records the
design hash, build hash and evidence hash (parity report or plan) it binds to.
"""
from __future__ import annotations

import hashlib
import re
from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db import utcnow
from api.app.models import AnalysisRun, AuditLog, CoverageItem, Finding, Gate, Process, Rule
from api.app.services import evidence as evidence_svc
from engine.ir.spec_hash import canonical_json, spec_hash

GATES: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6")
RULE_DECIDED = frozenset({"approved", "edited", "rejected"})
RULE_APPROVED = frozenset({"approved", "edited"})
SYSTEM = "system"
STALE_REASON = "spec hash changed since the gate was decided"
IR_CHANGED = "IR changed since analysis: re-run analysis"
PARTIAL_NOTICE = "partial analysis: a model stage failed, see the job log"
ANALYSIS_PARTIAL = "analysis is partial ({stages} failed); re-run analysis before approving G1"


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


def ir_hash(ir: dict[str, Any]) -> str:
    """SHA-256 of the canonical IR JSON (stored on each analysis run as ``summary.ir_hash``)."""
    return hashlib.sha256(canonical_json(ir).encode("utf-8")).hexdigest()


def is_stale(row: Gate | None, process: Process) -> bool:
    return row is not None and row.state != "pending" and row.spec_hash != process.spec_hash


def _not_approved(prev: str, row: Gate | None, process: Process) -> list[str]:
    if row is None or row.state != "approved":
        return [f"{prev} not approved"]
    if is_stale(row, process):
        return [f"{prev} approval is stale: the spec hash changed since it was decided"]
    return []


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
        summary = analysis.summary or {}
        seen = summary.get("ir_hash")
        if seen and seen != ir_hash(process.ir):
            g1.append(IR_CHANGED)
        if summary.get("notice") == PARTIAL_NOTICE:
            stages = ", ".join(summary.get("failed_stages") or []) or "a model stage"
            g1.append(ANALYSIS_PARTIAL.format(stages=stages))
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
    g2 += _not_approved("G1", rows.get("G1"), process)
    out["G2"] = g2
    evidence = await evidence_svc.evidence_reasons(session, process, rows)
    for prev, name in pairwise(GATES[1:]):
        row = rows.get(prev)
        out[name] = list(evidence.get(name, [])) + _not_approved(prev, row, process)
    return out


def gate_out(name: str, row: Gate | None, reasons: list[str], process: Process,
             notes: list[str] | None = None) -> dict[str, Any]:
    """``notes`` are shown with the gate without blocking it (G5: unconfirmed intended changes)."""
    return {
        "gate": name,
        "state": row.state if row else "pending",
        "stale": is_stale(row, process),
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
        "notes": list(notes or []),
    }


async def gates_view(session: AsyncSession, process: Process) -> list[dict[str, Any]]:
    """Read only: a gate whose spec hash moved shows ``stale: true`` until the next action."""
    rows = await gate_rows(session, process.id)
    reasons = await gate_reasons(session, process)
    notes = await evidence_svc.gate_notes(session, process)
    return [gate_out(name, rows.get(name), reasons[name], process, notes.get(name)) for name in GATES]


async def decide(session: AsyncSession, process: Process, gate: str, state: str, note: str | None,
                 user: str) -> dict[str, Any]:
    """Approve or reject one gate. Approval needs the gate enabled (else ``GateBlocked``, 409);
    the owner may always reject, and the note is recorded as the reason."""
    await reconcile(session, process)
    if state == "approved":
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
    hashes = await evidence_svc.decision_hashes(session, process, gate)
    extra: dict[str, Any] = {}
    if gate == "G4" and state == "approved":
        # What the engineer knowingly accepted, written onto the gate: a later reader of this
        # record must be able to see that the build did not pass, and what was decided anyway.
        build = await evidence_svc.latest_build(session, process.id)
        _, waived = await evidence_svc.g4_waivable(session, process, build, rows.get("G3"))
        if waived:
            kept = sum(1 for f in (build.reviews or []) if f.get("decision") == "keep")
            rejected = sum(1 for f in (build.reviews or []) if f.get("decision") == "reject")
            extra["accepted_over"] = waived
            summary = (f"approved over: {'; '.join(waived)}. Review findings: "
                       f"{kept} kept, {rejected} rejected.")
            note = f"{note} ({summary})" if note else summary
    if gate == "G5" and state == "approved":
        extra["unconfirmed_intended"] = await evidence_svc.unconfirmed_intended(session, process)
    row.state = state
    row.spec_hash = process.spec_hash
    row.design_hash = hashes["design_hash"]
    row.build_hash = hashes["build_hash"]
    row.evidence_hash = hashes["evidence_hash"]
    row.decided_by = user
    row.decided_at = utcnow()
    row.note = note
    row.reset_reason = None
    audit(session, process, user, f"gate {state}", gate=gate,
          detail={"spec_hash": process.spec_hash, "note": note, "previous_state": previous,
                  **({"reason": note or "rejected by the gate owner"} if state == "rejected" else {}),
                  **{k: v for k, v in hashes.items() if v}, **extra})
    if state == "rejected" and gate != GATES[-1]:
        await reset_gates(session, process, rows, GATES[GATES.index(gate) + 1], f"{gate} rejected",
                          user, "user", {"spec_hash": process.spec_hash})
    if gate == "G2":
        if state == "approved":
            process.migration_status = "validated"
        elif process.migration_status == "validated":
            process.migration_status = "analysed"
    await session.flush()
    notes = (await evidence_svc.gate_notes(session, process)).get(gate)
    return gate_out(gate, row, (await gate_reasons(session, process))[gate], process, notes)
