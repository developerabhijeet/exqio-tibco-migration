"""Evidence behind gates G3 to G6 (design spec 5.2): what must exist before each is enabled, and
the hashes a decision binds to.

* G3 design: a design record bound to the current spec hash.
* G4 build: the latest build is bound to the approved design; compile passed; every approved
  rule has a test with a body (not "no test body"); every test passed.
* G5 parity: the latest verify run of that build has no unexplained difference and no scenario
  that could not run. Intended differences (a finding decided Fix cites an activity in the
  field's data lineage) are not confirmed by a TIBCO sandbox run; they do not block G5 and their
  count is a G5 note. A difference a Fix finding touches only through a branch decision (control
  lineage) stays a defect and blocks G5 with its own reason.
* G6 deploy: a Terragrunt plan of that build, or connect-later acknowledged by an operations user
  with a note.

The "previous gate approved" condition is added by :mod:`api.app.services.gates`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models import Build, DesignRecord, Gate, Plan, Process, Rule, ScenarioRun, TestResult

APPROVED = ("approved", "edited")
FINISHED_BUILDS = ("passed", "failed")
PLAN_OK = ("planned", "acknowledged")
UNCONFIRMED_NOTE = "not confirmed: no TIBCO sandbox run; derived from the TIBCO model"
CONTROL_ONLY_NOTE = ("touched only through a branch decision by a Fix finding; not accepted as intended "
                     "without a TIBCO sandbox run")
NUGET_MARKER = "NuGet unreachable"  # the restore log's note (``build.NUGET_DOWN``) starts with it
NUGET_REMEDY = "NuGet unreachable: run `make nuget-cache` on a connected machine"
# logged by the build when the setting is empty, and appended to the test step's log when the tests
# then failed because Testcontainers could not reach Docker (the G4 reason repeats it)
TESTDB_UNSET = "TEST_DATABASE_URL not set: generated tests will start a Testcontainers Postgres (needs Docker)"


def canonical_hash(data: Any) -> str:
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def latest_design(session: AsyncSession, process_id: str) -> DesignRecord | None:
    return await session.scalar(select(DesignRecord).where(DesignRecord.process_id == process_id)
                                .order_by(DesignRecord.version.desc()).limit(1))


async def latest_build(session: AsyncSession, process_id: str, *, finished: bool = True) -> Build | None:
    q = select(Build).where(Build.process_id == process_id)
    if finished:
        q = q.where(Build.status.in_(FINISHED_BUILDS))
    return await session.scalar(q.order_by(Build.created_at.desc(), Build.id).limit(1))


async def latest_verify_runs(session: AsyncSession, build_id: str) -> list[ScenarioRun]:
    """The scenario runs of the most recent verify job of a build."""
    last = await session.scalar(select(ScenarioRun).where(ScenarioRun.build_id == build_id)
                                .order_by(ScenarioRun.created_at.desc()).limit(1))
    if last is None:
        return []
    rows = (await session.execute(select(ScenarioRun).where(ScenarioRun.build_id == build_id,
                                                            ScenarioRun.job_id == last.job_id)
                                  .order_by(ScenarioRun.created_at, ScenarioRun.id))).scalars().all()
    return list(rows)


def parity_hash(runs: list[ScenarioRun]) -> str | None:
    if not runs:
        return None
    return canonical_hash(sorted((r.scenario_id, r.report_hash or "") for r in runs))


def unconfirmed_count(runs: list[ScenarioRun]) -> int:
    """Intended differences of these runs that no TIBCO sandbox run confirmed."""
    return sum(int((r.report or {}).get("unconfirmed_intended") or 0) for r in runs)


def unconfirmed_text(n: int) -> str:
    return f"{n} intended difference(s) {UNCONFIRMED_NOTE}"


def control_only_count(runs: list[ScenarioRun]) -> int:
    """Defects of these runs that a Fix finding touches only through a branch decision."""
    return sum(int((r.report or {}).get("control_only_defects") or 0) for r in runs)


def control_only_text(n: int) -> str:
    """Appended to the unexplained count: control-only defects are a part of it, not extra."""
    return f"of which {n} are {CONTROL_ONLY_NOTE}"


async def unconfirmed_intended(session: AsyncSession, process: Process) -> int:
    """Unconfirmed intended differences in the latest verify run of the latest finished build."""
    build = await latest_build(session, process.id)
    return unconfirmed_count(await latest_verify_runs(session, build.id)) if build is not None else 0


async def gate_notes(session: AsyncSession, process: Process) -> dict[str, list[str]]:
    """Notes shown with a gate that do not block it: G5 carries the unconfirmed intended count."""
    n = await unconfirmed_intended(session, process)
    return {"G5": [unconfirmed_text(n)]} if n else {}


async def latest_plan(session: AsyncSession, build_id: str) -> Plan | None:
    return await session.scalar(select(Plan).where(Plan.build_id == build_id)
                                .order_by(Plan.created_at.desc(), Plan.id).limit(1))


def build_step(build: Build, name: str) -> dict[str, Any] | None:
    return next((s for s in build.steps or [] if s.get("name") == name), None)


def reviews_all_decided(build: Build | None) -> bool:
    """True when the engineer has kept or rejected every finding the review panel raised.

    This is the condition that lets G4 be approved over a failing build: the engineer has read
    what the reviewers found and said, for each one, whether it must be acted on. A build with no
    findings at all does not count as reviewed, because nobody has looked at anything."""
    findings = build.reviews if build else None
    return bool(findings) and all(f.get("decision") in ("keep", "reject") for f in findings)


async def g4_waivable(session: AsyncSession, process: Process, build: Build | None,
                      g3: Gate | None) -> tuple[list[str], list[str]]:
    """``(hard, waivable)`` G4 blockers.

    Hard blockers stay blockers whatever anyone decides: without a build, or with a build made
    from a different spec or design, an approval would bind the gate to something other than what
    was reviewed. The waivable ones say the code does not work yet, which an engineer who has
    read every review finding may knowingly accept; the gate then records exactly that."""
    all_reasons = await g4_blockers(session, process, build, g3)
    hard = [r for r in all_reasons if r.startswith("no build has finished") or "is stale" in r]
    return hard, [r for r in all_reasons if r not in hard]


async def g4_blockers(session: AsyncSession, process: Process, build: Build | None,
                      g3: Gate | None) -> list[str]:
    if build is None:
        return ["no build has finished"]
    reasons: list[str] = []
    if build.spec_hash != process.spec_hash:
        reasons.append("the latest build is stale: the spec hash changed since it was built")
    if g3 is not None and g3.state == "approved" and g3.design_hash and build.design_hash != g3.design_hash:
        reasons.append("the latest build is stale: it was not built from the approved design record")
    step = build_step(build, "build")
    if not (step and step.get("ok")):
        restore = build_step(build, "restore")
        restore_failed = restore is not None and not restore.get("ok")
        why = "restore failed" if restore_failed else "build failed"
        remedy = f"; {NUGET_REMEDY}" if restore_failed and NUGET_MARKER in (restore.get("log") or "") else ""
        reasons.append(f"compile did not pass ({why}){remedy}")
    tests = (await session.execute(select(TestResult).where(TestResult.build_id == build.id))).scalars().all()
    rules = (await session.execute(select(Rule).where(Rule.process_id == process.id))).scalars().all()
    approved = sorted({r.rule_key for r in rules if r.status in APPROVED})
    with_body = {t.rule_key for t in tests if t.rule_key and t.has_body}
    no_body = [k for k in approved if k not in with_body]
    if no_body:
        reasons.append(f"approved rule(s) without a test body: {', '.join(no_body)}")
    if not tests:
        reasons.append("no tests")
    else:
        from api.app.services.build import blocking_subprocess
        blocked = {t.id: blocking_subprocess(t.message) for t in tests}
        names = sorted({v for v in blocked.values() if v})
        if names:
            reasons.append(f"{sum(1 for v in blocked.values() if v)} test(s) blocked by sub-process(es) "
                           f"with no approved design: {', '.join(names)}. Design and build them with "
                           f"this process; nothing about this code is known to be wrong from these.")
        live = [t for t in tests if not blocked[t.id]]
        failed = sum(1 for t in live if t.outcome == "failed")
        not_run = sum(1 for t in live if t.outcome not in ("passed", "failed"))
        if failed or not_run:
            test_step = build_step(build, "test")
            hint = (f"; {TESTDB_UNSET}" if test_step and not test_step.get("ok")
                    and TESTDB_UNSET in (test_step.get("log") or "") else "")
            reasons.append(f"{failed + not_run} of {len(live)} runnable test(s) not passed "
                           f"({failed} failed, {not_run} not run){hint}")
    return reasons


async def evidence_reasons(session: AsyncSession, process: Process,
                           rows: dict[str, Gate]) -> dict[str, list[str]]:
    """Evidence blockers for G3 to G6 (an empty list means the evidence is there)."""
    out: dict[str, list[str]] = {}
    design = await latest_design(session, process.id)
    if design is None:
        out["G3"] = ["design record has not been generated"]
    elif design.spec_hash != process.spec_hash:
        out["G3"] = ["design record is stale: the spec hash changed since it was generated"]
    else:
        out["G3"] = []
    build = await latest_build(session, process.id)
    # G4: an engineer who has decided every review finding may approve over a build that does not
    # work yet. The waived reasons are not dropped, they are recorded on the gate when it is
    # approved, so Verify and Deploy still read the truth about what they inherited.
    hard, waivable = await g4_waivable(session, process, build, rows.get("G3"))
    out["G4"] = hard if reviews_all_decided(build) else hard + waivable
    if build is None:
        out["G5"] = ["no build has finished"]
        out["G6"] = ["no build has finished"]
        return out
    runs = await latest_verify_runs(session, build.id)
    if not runs:
        out["G5"] = ["the scenario set has not been run against the latest build"]
    else:
        g5: list[str] = []
        unexplained = sum(int((r.report or {}).get("unexplained") or 0) for r in runs)
        errors = sum(1 for r in runs if r.status == "error")
        control_only = control_only_count(runs)
        if unexplained or control_only:
            g5.append(f"{unexplained} unexplained difference(s) in the parity report"
                      + (f", {control_only_text(control_only)}" if control_only else ""))
        if errors:
            g5.append(f"{errors} scenario(s) could not run")
        out["G5"] = g5
    plan = await latest_plan(session, build.id)
    if plan is None or plan.status not in PLAN_OK:
        state = f" (last plan {plan.status})" if plan is not None else ""
        out["G6"] = ["no plan: connect Azure and run the plan, or acknowledge connect-later with a note"
                     + state]
    else:
        out["G6"] = []
    return out


async def decision_hashes(session: AsyncSession, process: Process, gate: str) -> dict[str, str | None]:
    """``design_hash``, ``build_hash`` and ``evidence_hash`` a decision on ``gate`` binds to."""
    out: dict[str, str | None] = {"design_hash": None, "build_hash": None, "evidence_hash": None}
    if gate in ("G1", "G2"):
        return out
    design = await latest_design(session, process.id)
    out["design_hash"] = design.design_hash if design else None
    if gate == "G3":
        return out
    build = await latest_build(session, process.id)
    if build is None:
        return out
    out["design_hash"] = build.design_hash or out["design_hash"]
    out["build_hash"] = build.build_hash
    if gate == "G5":
        out["evidence_hash"] = parity_hash(await latest_verify_runs(session, build.id))
    elif gate == "G6":
        plan = await latest_plan(session, build.id)
        out["evidence_hash"] = plan.plan_hash if plan else None
    return out
