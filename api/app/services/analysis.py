"""The analysis job and the Validate tab's reads and decisions (design spec 2.3 and 5.1).

Stages run in a fixed order, each tagged ``code`` or ``ai`` in the job log:
static rules (code) -> analyst (ai) -> verifier (code) -> coverage (code) -> risk reviewer (ai)
-> challenger (ai) -> persist (code). Without a model provider the ai stages log
"skipped: code-only analysis: no model configured" and the result carries ``ai_available``
false with the reason. A model stage that fails is logged and the job continues code only; the
run is stored with ``PARTIAL_NOTICE`` and ``failed_stages`` (which keeps G1 disabled until a
complete run). Only a failed Workflow Analyst with no earlier analysis to carry stores nothing.
The run records the hash of the IR it analyzed (taken when the job starts), so a rescan during
the job shows as "IR changed since analysis".

Ids: rules are ``R1..Rn`` per process and keep their key across re-runs when the analyst's
text is unchanged (matched on normalised text, against the analyst's original wording when the
SME edited it); SME decisions on matched rules survive. Static findings keep the engine ids
(``S<n>-<process>-<k>``); AI findings are ``F1..Fn``, matched the same way.

After persisting, the process's summary, rules and findings are written to the knowledge store
as chunks for Ask, and SME decisions are recorded in migration memory.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.db import utcnow
from api.app.jobs.runner import JobContext
from api.app.models import (
    AnalysisRun,
    Application,
    Challenge,
    CoverageItem,
    Finding,
    Process,
    Rule,
)
from api.app.services import gates as gate_svc
from api.app.services.knowledge import Knowledge, KnowledgeUnavailable
from api.app.services.providers import (
    CODE_ONLY_NOTICE,
    build_provider,
    model_call_recorder,
    stage_of,
)
from engine.agents.analyst import analyse
from engine.agents.challenger import challenge
from engine.agents.jsonout import JsonOutputError
from engine.agents.provider import ProviderError
from engine.agents.risk import review_risk
from engine.agents.routing import ModelCallRecord
from engine.analysis.coverage import coverage_matrix
from engine.analysis.evidence import EvidenceIndex
from engine.analysis.models import Challenge as EngineChallenge
from engine.analysis.models import Citation
from engine.analysis.models import Finding as EngineFinding
from engine.analysis.models import Rule as EngineRule
from engine.analysis.static_rules import run_static_rules
from engine.analysis.verifier import verify_citations
from engine.ir.models import Application as IRApplication
from engine.ir.models import Process as IRProcess
from engine.knowledge.store import KnowledgeError, guard_text

log = logging.getLogger(__name__)

STAGES = ("static rules", "analyst", "verifier", "coverage", "risk reviewer", "challenger", "persist")
SKIPPED = f"skipped: {CODE_ONLY_NOTICE}"
PARTIAL_NOTICE = gate_svc.PARTIAL_NOTICE
UNVERIFIED_OWNER = "business SME"
_WS = re.compile(r"\s+")


class Conflict(Exception):
    """A decision that cannot be applied in the current state (HTTP 409)."""


def norm(text: str | None) -> str:
    return _WS.sub(" ", (text or "").strip()).lower()


# ------------------------------------------------------------------ id assignment
def assign_rule_keys(rules: list[EngineRule], existing: list[dict[str, Any]], mark: int = 0) -> dict[str, str]:
    """Give each analyst rule its key (``R<n>``) and restore SME decisions on matched rules.
    New keys start above ``mark`` (the process's high-water mark), so a key is never reused.
    Returns ``{key: analyst text}`` for ``original_text``."""
    by_text: dict[str, dict[str, Any]] = {}
    for e in existing:
        for t in (e["original_text"], e["text"]):
            if t:
                by_text.setdefault(norm(t), e)
    next_n = max([mark, *(gate_svc.key_number(e["key"]) for e in existing)]) + 1
    used: set[str] = set()
    originals: dict[str, str] = {}
    for r in rules:
        analyst_text = r.text
        e = by_text.get(norm(r.text))
        if e is not None and e["key"] not in used:
            r.id = e["key"]
            if e["status"] in gate_svc.RULE_DECIDED:
                r.status = e["status"]
                if e["status"] == "edited":
                    r.text = e["text"]
            r.owner = e["owner"] if r.owner is None else r.owner
        else:
            r.id = f"R{next_n}"
            next_n += 1
        used.add(r.id)
        originals[r.id] = analyst_text
    return originals


def assign_finding_keys(findings: list[EngineFinding], existing: list[dict[str, Any]], mark: int = 0) -> None:
    """``F<n>`` keys for AI findings, matched on normalised text, new ones above ``mark``."""
    ai_by_text = {norm(e["text"]): e for e in existing if e["source"] == "ai"}
    next_n = max([mark, *(gate_svc.key_number(e["key"]) for e in existing if e["source"] == "ai")]) + 1
    used: set[str] = set()
    for f in findings:
        if f.source != "ai":
            continue
        e = ai_by_text.get(norm(f.text))
        if e is not None and e["key"] not in used:
            f.id = e["key"]
        else:
            f.id = f"F{next_n}"
            next_n += 1
        used.add(f.id)


# ------------------------------------------------------------------ knowledge chunks
def chunk_items(process_name: str, summary: str | None, rules: list[Any],
                findings: list[Any]) -> list[tuple[str, str]]:
    """Prose chunks for Ask. Rows or engine models both work (duck typed)."""
    items: list[tuple[str, str]] = []
    if summary:
        items.append(("summary", f"Summary of {process_name}: {summary}"))
    for r in rules:
        key = getattr(r, "rule_key", None) or r.id
        steps = ", ".join(dict.fromkeys(_cit(c)["activity_id"] for c in r.citations or []))
        items.append(("rule", f"Rule {key} of {process_name} ({r.status}): {r.text}"
                              + (f" Steps: {steps}." if steps else "")))
    for f in findings:
        key = getattr(f, "finding_key", None) or f.id
        text = f"Finding {key} ({f.severity}) in {process_name}: {f.text}"
        if f.business_impact:
            text += f" Business impact: {f.business_impact}"
        if getattr(f, "decision", None):
            text += f" Decision: {f.decision}" + (f" ({f.note})" if f.note else "") + "."
        items.append(("finding", text))
    out = []
    for kind, text in items:
        try:
            guard_text(text)
        except KnowledgeError:
            continue  # raw TIBCO markup is never embedded
        out.append((kind, text))
    return out


def _cit(c: Any) -> dict[str, Any]:
    return c if isinstance(c, dict) else c.model_dump()


async def reindex(knowledge: Knowledge, session: AsyncSession, process: Process) -> str | None:
    """Rewrite the process's chunks from the stored rows; returns an error text or None."""
    analysis = await gate_svc.latest_analysis(session, process.id)
    summary = (analysis.summary or {}).get("text") if analysis else None
    rules = await gate_svc.load_rules(session, process.id)
    findings = await gate_svc.load_findings(session, process.id)
    try:
        await knowledge.index_process(process.project_id, process.id,
                                      chunk_items(process.name, summary, rules, findings))
    except KnowledgeUnavailable as exc:
        log.warning("knowledge chunks not written for %s: %s", process.id, exc)
        return str(exc)
    return None


# ------------------------------------------------------------------ the job
def analyse_job(process_id: str, settings: Settings, knowledge: Knowledge):
    async def run(ctx: JobContext) -> dict[str, Any]:
        return await run_analysis(ctx, process_id, settings, knowledge)

    return run


class AnalysisFailed(Exception):
    """The Workflow Analyst failed and there is no earlier analysis to carry forward: nothing is
    stored. A later stage failing on a first run stores what succeeded as a partial analysis."""


def _existing(rules: list[Rule], findings: list[Finding]) -> tuple[list[dict], list[dict]]:
    return ([{"key": r.rule_key, "text": r.text, "original_text": r.original_text, "status": r.status,
              "owner": r.owner} for r in rules],
            [{"key": f.finding_key, "text": f.text, "source": f.source} for f in findings])


def _carried_rules(rows: list[Rule]) -> list[EngineRule]:
    """The stored rules as engine rules, to be re-verified and re-covered against the current IR.
    An ``unverified`` rule goes back to ``draft`` so the verifier decides it again."""
    out = []
    for r in rows:
        ch = r.challenge
        out.append(EngineRule(
            id=r.rule_key, text=r.text, confidence=r.confidence,
            status="draft" if r.status == "unverified" else r.status, owner=r.owner,
            memory_refs=list(r.memory_refs or []),
            citations=[Citation.model_validate(_cit(c)) for c in r.citations or []],
            challenge=EngineChallenge.model_validate(ch) if ch else None))
    return out


def _carried_ai_findings(rows: list[Finding]) -> list[EngineFinding]:
    return [EngineFinding(id=f.finding_key, severity=f.severity, text=f.text, source="ai",
                          business_impact=f.business_impact,
                          citations=[Citation.model_validate(_cit(c)) for c in f.citations or []])
            for f in rows if f.source == "ai"]


async def run_analysis(ctx: JobContext, process_id: str, settings: Settings,
                       knowledge: Knowledge) -> dict[str, Any]:
    async with ctx.sessionmaker() as s:
        row = await s.get(Process, process_id)
        if row is None:
            raise ValueError(f"process {process_id} not found")
        app_row = await s.get(Application, row.application_id)
        project_id = row.project_id
        proc = IRProcess.model_validate(row.ir)
        analyzed_ir_hash = gate_svc.ir_hash(row.ir)  # the IR this job analyzes, before any stage
        app = IRApplication.model_validate({**(app_row.ir if app_row else {}), "processes": []})
        rule_rows = await gate_svc.load_rules(s, process_id)
        finding_rows = await gate_svc.load_findings(s, process_id)
        old_rules, old_findings = _existing(rule_rows, finding_rows)
        prev = await gate_svc.latest_analysis(s, process_id)
        prev_summary = (prev.summary or {}) if prev else {}
        prev_technical = {i.ref: i.technical_reason for i in (await s.execute(
            select(CoverageItem).where(CoverageItem.process_id == process_id))).scalars() if i.technical_reason}
    has_previous = prev is not None
    marks = dict(prev_summary.get("key_marks") or {})
    snapshot = {r.rule_key: (r.status, r.text) for r in rule_rows}

    async def on_record(rec: ModelCallRecord) -> None:
        state = "failed: " + (rec.error or "") if rec.error else (
            f"{rec.usage.get('in', 0)} tokens in, {rec.usage.get('out', 0)} out")
        await ctx.log(f"model call {stage_of(rec)}: {rec.provider} {rec.model or '-'} (tier {rec.tier}, "
                      f"task complexity {rec.complexity:g}{', after fallback' if rec.fell_back else ''}); "
                      f"{state}", tag="ai")

    choice = build_provider(settings, process=proc)
    provider = choice.provider
    if provider is not None:
        provider.recorder = model_call_recorder(
            ctx.sessionmaker, job_id=ctx.job_id, project_id=project_id, process_id=process_id,
            primary_name=provider.primary.name, on_record=on_record)
    ai_errors: list[str] = []
    failed_stages: list[str] = []

    def stage_failed(stage: str, exc: Exception) -> None:
        ai_errors.append(f"{stage}: {exc}")
        failed_stages.append(stage)
        if stage == "analyst" and not has_previous:
            raise AnalysisFailed(f"{stage} failed and there is no earlier analysis to keep, so nothing "
                                 f"was stored: {exc}") from exc

    # 1. static risk rules (code)
    await ctx.stage(STAGES[0])
    static = run_static_rules(proc, app.contracts, app.substvars)
    await ctx.log(f"static risk rules S1 to S7: {len(static)} finding(s)"
                  + (f": {', '.join(f.id for f in static)}" if static else ""))

    # 2. Workflow Analyst (ai)
    await ctx.stage(STAGES[1])
    rules: list[EngineRule] = []
    technical: dict[str, str] = {}
    summary: str | None = None
    rules_from_model = False
    keep_note = f"; keeping the {len(rule_rows)} earlier rule(s)" if rule_rows else ""
    if provider is None:
        await ctx.log(SKIPPED + keep_note, tag="ai")
    else:
        memory_items: list[Any] = []
        query = " ".join([proc.name, *[a.name for a in proc.activities[:30]]])
        try:
            memory_items = await knowledge.retrieve_memory(project_id, query)
        except KnowledgeUnavailable as exc:
            await ctx.log(f"migration memory unavailable: {exc}")
        if memory_items:
            await ctx.log(f"{len(memory_items)} prior decision(s) or pattern(s) from migration memory "
                          "passed to the analyst")
        try:
            out = await analyse(proc, app.contracts, memory_items, provider)
            rules, technical, summary = out.rules, out.technical, out.summary or None
            rules_from_model = True
            await ctx.log(f"Workflow Analyst proposed {len(rules)} rule(s) and marked "
                          f"{len(technical)} item(s) technical", tag="ai")
        except (ProviderError, JsonOutputError) as exc:
            await ctx.log(f"Workflow Analyst failed: {exc}" + keep_note, tag="ai")
            stage_failed("analyst", exc)
    if rules_from_model:
        originals = assign_rule_keys(rules, old_rules, int(marks.get("rule", 0)))
    else:  # carry the earlier rules (and their technical items and summary) forward
        rules = _carried_rules(rule_rows)
        originals = {r.rule_key: r.original_text or r.text for r in rule_rows}
        technical = prev_technical
        if prev_summary.get("summary_source") in ("model", "carried"):
            summary = prev_summary.get("text")

    # 3. citation verifier (code)
    await ctx.stage(STAGES[2])
    verification = verify_citations(proc, rules)
    for r in rules:
        if r.status == "unverified" and not r.owner:
            r.owner = UNVERIFIED_OWNER
    await ctx.log(f"citations verified {verification.verified} of {verification.total}")
    for failure in verification.failures:
        await ctx.log(f"{failure.rule_id}: {failure.reason}")

    # 4. coverage matrix (code)
    await ctx.stage(STAGES[3])
    coverage = coverage_matrix(proc, rules, technical)
    await ctx.log(f"coverage {coverage.covered} of {coverage.total} steps, branches and mappings")

    # 5. Risk Reviewer (ai)
    await ctx.stage(STAGES[4])
    findings: list[EngineFinding] = list(static)
    findings_from_model = False
    ai_rows = [f for f in finding_rows if f.source == "ai"]
    keep_note = f"; keeping the {len(ai_rows)} earlier AI finding(s)" if ai_rows else ""
    index = EvidenceIndex(proc)
    if provider is None:
        await ctx.log(SKIPPED + keep_note, tag="ai")
    else:
        try:
            findings = await review_risk(proc, static, provider,
                                         lambda _p, c: index.has(c.activity_id, c.kind, c.text))
            findings_from_model = True
            added = sum(1 for f in findings if f.source == "ai")
            await ctx.log(f"Risk Reviewer described the impact of {len(static)} static finding(s) and "
                          f"added {added} finding(s)", tag="ai")
        except (ProviderError, JsonOutputError) as exc:
            await ctx.log(f"Risk Reviewer failed: {exc}" + keep_note, tag="ai")
            stage_failed("risk reviewer", exc)
    if findings_from_model:
        assign_finding_keys(findings, old_findings, int(marks.get("finding", 0)))
    else:  # earlier impacts on the static findings, earlier AI findings re-checked
        impacts = {f.finding_key: f.business_impact for f in finding_rows if f.business_impact}
        findings = [f.model_copy(update={"business_impact": impacts.get(f.id)}) for f in static]
        for f in _carried_ai_findings(finding_rows):
            if not all(index.has(c.activity_id, c.kind, c.text) for c in f.citations):
                await ctx.log(f"{f.id}: a citation is no longer found in the current IR (kept for the SME)")
            findings.append(f)

    # 6. Independent Challenger (ai)
    await ctx.stage(STAGES[5])
    if provider is None:
        await ctx.log(SKIPPED + ("; keeping the earlier verdicts" if rules else ""), tag="ai")
    elif not rules:
        await ctx.log("no rules to challenge", tag="ai")
    else:
        try:
            rules = await challenge(proc, rules, provider, app.contracts)
            disagree = [r.id for r in rules if r.challenge and r.challenge.verdict == "disagree"]
            await ctx.log(f"Independent Challenger agreed with {len(rules) - len(disagree)} rule(s), "
                          f"disagreed with {len(disagree)}"
                          + (f" ({', '.join(disagree)}): flagged for the SME" if disagree else ""),
                          tag="ai")
        except (ProviderError, JsonOutputError) as exc:
            await ctx.log(f"Independent Challenger failed, keeping earlier verdicts where the rule text "
                          f"is unchanged: {exc}", tag="ai")
            stage_failed("challenger", exc)
            earlier = {r.rule_key: r for r in rule_rows}
            for r in rules:
                e = earlier.get(r.id)
                if r.challenge is None and e is not None and e.challenge and norm(e.text) == norm(r.text):
                    r.challenge = EngineChallenge.model_validate(e.challenge)

    # 7. persist (code)
    await ctx.stage(STAGES[6])
    full_run = rules_from_model and findings_from_model and not ai_errors
    ai_available = provider is not None and not ai_errors
    ai_reason = choice.reason or ("; ".join(ai_errors) or None)
    summary_source = "model" if rules_from_model and summary else ("carried" if summary else "code")
    if summary is None:
        summary = (f"Code-only analysis of {proc.name}: {len(proc.activities)} activities, "
                   f"{len(proc.transitions)} transitions, {len(static)} static finding(s). "
                   + ("No model is configured, so no business rules were proposed."
                      if provider is None else "The model stages did not complete."))
    notice = CODE_ONLY_NOTICE if provider is None else (PARTIAL_NOTICE if ai_errors else None)
    meta = {"text": summary, "summary_source": summary_source, "technical": technical,
            "ai_available": ai_available, "ai_reason": ai_reason, "notice": notice,
            "key_marks": marks, "full_run": full_run, "failed_stages": failed_stages,
            "ir_hash": analyzed_ir_hash}
    result = await _persist(ctx, process_id, choice.name, meta, rules, originals, findings, coverage,
                            snapshot, keep_decided=not full_run)
    await ctx.log(f"stored {result['rules']} rule(s), {result['findings']} finding(s), "
                  f"{result['coverage_total']} coverage item(s)"
                  + ("" if full_run else " (earlier decisions kept: not every model stage ran)")
                  + f"; spec hash {result['spec_hash'][:12]}"
                  + (f"; gates reset: {', '.join(result['gates_reset'])}" if result["gates_reset"] else ""))

    async with ctx.sessionmaker() as s:
        process = await s.get(Process, process_id)
        error = await reindex(knowledge, s, process)
    await ctx.log(f"knowledge store not updated: {error}" if error else
                  "summary, rules and findings indexed for Ask")
    return {**result, "ai_available": ai_available, "ai_reason": ai_reason, "provider": choice.name,
            "full_run": full_run}


async def _persist(ctx: JobContext, process_id: str, provider_name: str, meta: dict[str, Any],
                   rules: list[EngineRule], originals: dict[str, str], findings: list[EngineFinding],
                   coverage: Any, snapshot: dict[str, tuple[str, str]], *,
                   keep_decided: bool) -> dict[str, Any]:
    """Store one analysis. The process row is locked, so a rule or finding decision cannot
    interleave; a rule the SME changed while the job ran keeps the SME's status and text. On a
    code-only or partial run (``keep_decided``) no decided rule or finding is deleted.
    ``meta["ir_hash"]`` is the hash of the IR the job analyzed (the current one when absent)."""
    async with ctx.sessionmaker() as s:
        process = await s.get(Process, process_id, with_for_update=True)
        await gate_svc.reconcile(s, process)
        meta = {**meta, "ir_hash": meta.get("ir_hash") or gate_svc.ir_hash(process.ir)}
        analysis = AnalysisRun(process_id=process_id, job_id=ctx.job_id, provider=provider_name,
                               static_only=not meta["ai_available"], summary=meta, verification={})
        s.add(analysis)
        await s.flush()

        old_rules = {r.rule_key: r for r in await gate_svc.load_rules(s, process_id)}
        old_rule_keys = set(old_rules)
        rows: dict[str, Rule] = {}
        for r in rules:
            row = old_rules.pop(r.id, None)
            if row is None:
                row = Rule(process_id=process_id, rule_key=r.id)
                s.add(row)
            concurrent = r.id in snapshot and (row.status, row.text) != snapshot[r.id]
            row.analysis_id = analysis.id
            if not concurrent:  # else the SME decided while the job ran: theirs stands
                row.text = r.text
                row.status = r.status
            row.original_text = originals.get(r.id, r.text)
            row.citations = [c.model_dump(mode="json") for c in r.citations]
            row.confidence = r.confidence
            row.owner = r.owner if row.owner is None or r.owner else row.owner
            row.memory_refs = list(r.memory_refs)
            row.challenge = r.challenge.model_dump(mode="json") if r.challenge else None
            rows[r.id] = row
        for gone in old_rules.values():
            if gone.status in gate_svc.RULE_DECIDED:
                if keep_decided:
                    rows[gone.rule_key] = gone
                    continue
                gate_svc.audit(s, process, gate_svc.SYSTEM, "rule removed by re-analysis",
                               actor_kind=gate_svc.SYSTEM,
                               detail={"rule_key": gone.rule_key, "text": gone.text, "status": gone.status,
                                       "job_id": ctx.job_id})
            await s.delete(gone)
        await s.flush()

        await s.execute(delete(Challenge).where(Challenge.process_id == process_id))
        for row in rows.values():
            ch = row.challenge
            if ch:
                s.add(Challenge(process_id=process_id, rule_id=row.id, verdict=ch["verdict"],
                                reason=ch.get("reason", ""), model=ch.get("model", "")))

        old_findings = {f.finding_key: f for f in await gate_svc.load_findings(s, process_id)}
        old_finding_keys = set(old_findings)
        kept_findings: set[str] = set()
        for f in findings:
            row = old_findings.pop(f.id, None)
            if row is None:
                row = Finding(process_id=process_id, finding_key=f.id)
                s.add(row)
            row.analysis_id = analysis.id
            row.severity = f.severity
            row.text = f.text
            row.citations = [c.model_dump(mode="json") for c in f.citations]
            row.source = f.source
            row.business_impact = f.business_impact
            kept_findings.add(f.id)
        for gone in old_findings.values():
            if gone.decision:
                if keep_decided:
                    kept_findings.add(gone.finding_key)
                    continue
                gate_svc.audit(s, process, gate_svc.SYSTEM, "finding removed by re-analysis",
                               actor_kind=gate_svc.SYSTEM,
                               detail={"finding_key": gone.finding_key, "decision": gone.decision,
                                       "job_id": ctx.job_id})
            await s.delete(gone)

        await s.execute(delete(CoverageItem).where(CoverageItem.process_id == process_id))
        s.add_all(CoverageItem(process_id=process_id, analysis_id=analysis.id, kind=i.kind, ref=i.ref,
                               covered_by=list(i.covered_by), technical_reason=i.technical_reason)
                  for i in coverage.items)
        await s.flush()
        # verification and coverage over what is actually stored (kept rows, concurrent decisions)
        verification, coverage = await refresh_checks(s, process, analysis,
                                                      await gate_svc.load_rules(s, process_id))
        marks = dict(meta.get("key_marks") or {})
        marks["rule"] = max([int(marks.get("rule", 0)), *(gate_svc.key_number(k) for k in rows)])
        marks["finding"] = max([int(marks.get("finding", 0)),
                                *(gate_svc.key_number(k) for k in kept_findings if k.startswith("F"))])
        analysis.summary = {**meta, "key_marks": marks}

        if process.migration_status == "not_started":
            process.migration_status = "analysed"
        changed = old_rule_keys != set(rows) or old_finding_keys != kept_findings
        gate_svc.audit(s, process, gate_svc.SYSTEM, "analysis completed", actor_kind=gate_svc.SYSTEM,
                       detail={"job_id": ctx.job_id, "provider": provider_name, "rules": len(rows),
                               "findings": len(kept_findings), "verified": verification.verified,
                               "citations": verification.total, "covered": coverage.covered,
                               "coverage_total": coverage.total, "ai_available": meta["ai_available"],
                               "full_run": not keep_decided})
        _, new_hash, reset = await gate_svc.apply_spec_hash(
            s, process, reason="analysis re-run", actor=gate_svc.SYSTEM, actor_kind=gate_svc.SYSTEM,
            force=changed)
        analysis.spec_hash = new_hash
        await s.commit()
        return {"analysis_id": analysis.id, "spec_hash": new_hash, "gates_reset": reset,
                "rules": len(rows), "findings": len(kept_findings), "verified": verification.verified,
                "citations": verification.total, "covered": coverage.covered,
                "coverage_total": coverage.total}


# ------------------------------------------------------------------ checks after a decision
async def refresh_checks(session: AsyncSession, process: Process, analysis: AnalysisRun | None,
                         rules: list[Rule]) -> tuple[Any, Any]:
    """Re-run the citation verifier and the coverage matrix over the stored rules; the
    verification is stored on ``analysis`` and the coverage rows are updated."""
    if analysis is None:
        return None, None
    proc = IRProcess.model_validate(process.ir)
    engine_rules = [EngineRule(id=r.rule_key, text=r.text, confidence=r.confidence, status=r.status,
                               citations=[Citation.model_validate(_cit(c)) for c in r.citations or []])
                    for r in rules]
    verification = verify_citations(proc, engine_rules)
    for row, er in zip(rules, engine_rules, strict=True):
        if er.status != row.status:
            row.status = er.status
            if er.status == "unverified" and not row.owner:
                row.owner = UNVERIFIED_OWNER
    analysis.verification = verification.model_dump(mode="json")
    items = (await session.execute(select(CoverageItem)
                                   .where(CoverageItem.process_id == process.id))).scalars().all()
    technical = {i.ref: i.technical_reason for i in items if i.technical_reason}
    coverage = coverage_matrix(proc, engine_rules, technical)
    by_ref = {(i.kind, i.ref): i for i in items}
    for item in coverage.items:
        row = by_ref.get((item.kind, item.ref))
        if row is None:
            session.add(CoverageItem(process_id=process.id, analysis_id=analysis.id, kind=item.kind,
                                     ref=item.ref, covered_by=list(item.covered_by),
                                     technical_reason=item.technical_reason))
        else:
            row.covered_by = list(item.covered_by)
    return verification, coverage


async def update_rule(session: AsyncSession, knowledge: Knowledge, rule_id: str, status: str | None,
                      text: str | None, user: str) -> Process:
    row = await session.get(Rule, rule_id)
    if row is None:
        raise LookupError("rule not found")
    process = await session.get(Process, row.process_id, with_for_update=True)  # not during a persist
    await session.refresh(row)
    analysis = await gate_svc.latest_analysis(session, process.id)
    await gate_svc.reconcile(session, process)
    memory: tuple[str, dict[str, Any]] | None = None
    reindex_needed = False
    if text is not None and text.strip() and norm(text) != norm(row.text):
        old_text, new_text = row.text, text.strip()
        row.text, row.status = new_text, "edited"
        action = "rule edited"
        detail = {"rule_key": row.rule_key, "old_text": old_text, "new_text": new_text}
        memory = ((f"SME {user} edited rule {row.rule_key} of {process.name}: \"{old_text}\" became "
                   f"\"{new_text}\"."),
                  {"process_id": process.id, "process_name": process.name, "rule_id": row.id,
                   "rule_key": row.rule_key, "old_text": old_text, "new_text": new_text, "user": user,
                   "decision": "edited"})
        reindex_needed = True
    elif status is not None:
        if status == row.status:
            return process
        if status == "approved" and row.status == "unverified":
            raise Conflict(f"{row.rule_key} has failing citations: reject it, or edit it once its "
                           "citations are fixed")
        previous = row.status
        row.status = status
        action = {"approved": "rule approved", "rejected": "rule rejected", "draft": "rule reopened"}[status]
        detail = {"rule_key": row.rule_key, "previous_status": previous, "status": status}
        if status == "rejected":
            memory = (f"SME {user} rejected rule {row.rule_key} of {process.name}: \"{row.text}\".",
                      {"process_id": process.id, "process_name": process.name, "rule_id": row.id,
                       "rule_key": row.rule_key, "text": row.text, "user": user, "decision": "rejected"})
        reindex_needed = True
    else:
        return process
    row.updated_by = user
    rules = await gate_svc.load_rules(session, process.id)
    await refresh_checks(session, process, analysis, rules)
    gate_svc.audit(session, process, user, action, detail=detail)
    await gate_svc.apply_spec_hash(session, process, reason=action, actor=user, reset_from="G1", force=True)
    await session.commit()
    await _remember(knowledge, process.project_id, memory, user)
    if reindex_needed:
        await reindex(knowledge, session, process)
    return process


async def decide_finding(session: AsyncSession, knowledge: Knowledge, finding_id: str, decision: str,
                         note: str | None, user: str) -> Process:
    row = await session.get(Finding, finding_id)
    if row is None:
        raise LookupError("finding not found")
    process = await session.get(Process, row.process_id, with_for_update=True)
    await session.refresh(row)
    await gate_svc.reconcile(session, process)
    note = (note or "").strip() or None
    if row.decision == decision and row.note == note:
        return process
    previous = row.decision
    row.decision, row.note, row.decided_by, row.decided_at = decision, note, user, utcnow()
    gate_svc.audit(session, process, user, "finding decided",
                   detail={"finding_key": row.finding_key, "decision": decision, "note": note,
                           "previous_decision": previous})
    await gate_svc.apply_spec_hash(session, process, reason="finding decided", actor=user,
                                   reset_from="G2", force=True)
    await session.commit()
    memory = (f"SME {user} decided to {decision} finding {row.finding_key} of {process.name} "
              f"({row.text})" + (f": {note}" if note else "."),
              {"process_id": process.id, "process_name": process.name, "finding_id": row.id,
               "finding_key": row.finding_key, "decision": decision, "note": note, "user": user})
    await _remember(knowledge, process.project_id, memory, user)
    await reindex(knowledge, session, process)
    return process


async def _remember(knowledge: Knowledge, project_id: str, memory: tuple[str, dict[str, Any]] | None,
                    user: str) -> str | None:
    if memory is None:
        return None
    try:
        return await knowledge.record_decision(project_id, memory[0], memory[1], user)
    except KnowledgeUnavailable as exc:
        log.warning("decision not recorded in migration memory: %s", exc)
        return None


# ------------------------------------------------------------------ view
def _rule_out(r: Rule, failures: list[dict[str, Any]]) -> dict[str, Any]:
    cits = []
    for c in r.citations or []:
        c = _cit(c)
        fail = next((f for f in failures if f.get("rule_id") == r.rule_key
                     and (f.get("citation") or {}).get("activity_id") == c.get("activity_id")
                     and (f.get("citation") or {}).get("kind") == c.get("kind")
                     and (f.get("citation") or {}).get("text") == c.get("text")), None)
        cits.append({"activity_id": c.get("activity_id"), "kind": c.get("kind"), "text": c.get("text"),
                     "verified": fail is None, "reason": fail.get("reason") if fail else None})
    return {"id": r.id, "rule_key": r.rule_key, "text": r.text, "original_text": r.original_text,
            "citations": cits, "confidence": r.confidence, "status": r.status, "owner": r.owner,
            "memory_refs": r.memory_refs or [], "challenge": r.challenge, "updated_by": r.updated_by,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def _finding_out(f: Finding) -> dict[str, Any]:
    return {"id": f.id, "finding_key": f.finding_key, "severity": f.severity, "text": f.text,
            "citations": [_cit(c) for c in f.citations or []], "source": f.source,
            "business_impact": f.business_impact, "decision": f.decision, "note": f.note,
            "decided_by": f.decided_by, "decided_at": f.decided_at.isoformat() if f.decided_at else None}


async def analysis_view(session: AsyncSession, process: Process) -> dict[str, Any]:
    """Everything tab 3 shows for one process. Read only: never writes (stale gates show
    ``stale: true`` until the next decision or analysis resets them)."""
    analysis = await gate_svc.latest_analysis(session, process.id)
    rules = await gate_svc.load_rules(session, process.id)
    findings = await gate_svc.load_findings(session, process.id)
    items = (await session.execute(select(CoverageItem).where(CoverageItem.process_id == process.id)
                                   .order_by(CoverageItem.id))).scalars().all()
    summary = (analysis.summary or {}) if analysis else {}
    ver = (analysis.verification or {}) if analysis else {}
    failures = list(ver.get("failures") or [])
    status = {r.rule_key: r.status for r in rules}
    live = [f for f in failures if status.get(f.get("rule_id")) != "rejected"]
    covered = sum(1 for i in items if i.covered_by or i.technical_reason)
    return {
        "process_id": process.id,
        "analysed": analysis is not None,
        "analysis_id": analysis.id if analysis else None,
        "job_id": analysis.job_id if analysis else None,
        "created_at": analysis.created_at.isoformat() if analysis and analysis.created_at else None,
        "provider": analysis.provider if analysis else None,
        "ai_available": bool(summary.get("ai_available")) if analysis else False,
        "ai_reason": summary.get("ai_reason"),
        "notice": summary.get("notice"),
        "static_only": analysis.static_only if analysis else None,
        "summary": summary.get("text"),
        "rules": [_rule_out(r, failures) for r in rules],
        "findings": [_finding_out(f) for f in findings],
        "verification": {"verified": ver.get("verified", 0), "total": ver.get("total", 0),
                         "failures": failures, "passed": not live},
        "coverage": {"covered": covered, "total": len(items), "complete": covered == len(items),
                     "items": [{"kind": i.kind, "ref": i.ref, "covered_by": i.covered_by or [],
                                "technical_reason": i.technical_reason} for i in items]},
        "spec_hash": process.spec_hash,
        "gates": await gate_svc.gates_view(session, process),
        "blockers": gate_svc.unknown_activity_blockers(process.ir),
        "ir_changed": bool(analysis and (summary.get("ir_hash") or gate_svc.ir_hash(process.ir))
                           != gate_svc.ir_hash(process.ir)),
    }
