"""The ``connector.jira.issues`` job: create or update one Jira issue per selected item (a finding,
an open question, an unexplained parity difference) of a process; each issue is recorded as a
``jira_issue`` link (``local_ref``: the item's stable identity, ruling X17, see
``connectors.jira``) and audited ``connector jira create|update``.

- A live link is updated (summary and description). A 404 there marks the link stale.
- A stale link is skipped, audited ``refused`` and reported ``stale``, unless ``create_again``:
  then the old issue is updated if it exists again, else a new one is created.
- No link: an issue carrying the item's label (a create whose answer was lost) is adopted, else
  the issue is created.

Items are independent: one that fails is audited ``failed`` and reported, and the others still
run; the job then fails, naming each failed item and what was done. Only a refused credential
(401) stops the rest, each of which is audited as not attempted.

Each item is handled under an advisory lock of its own (``jira:<process id>:<local ref>``), so two
jobs never both create an issue for the same item.

Payload ``{process_id, items: [{type, ref}], create_again, actor}`` (no credential: it is read
from its mounted file when the job runs); result ``{created: [key], updated: [key], stale: [ref]}``.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorDisabled, ConnectorError, ConnectorNotConfigured
from api.app.connectors.http import header_secrets, redact
from api.app.connectors.jira import (
    PROJECT_KEY,
    IssueItem,
    IssueSource,
    JiraConnector,
    canonical,
    issue_fields,
    parity_key,
    question_of,
    ref_label,
    update_fields,
)
from api.app.connectors.links import find_link, upsert_link
from api.app.connectors.registry import AtlassianConnector, connector_for
from api.app.jobs.locks import advisory_lock
from api.app.models import Application, FieldDiff, Finding, Process, Scenario
from api.app.services.evidence import latest_build, latest_design, latest_verify_runs

if TYPE_CHECKING:
    from api.app.jobs.registry import WorkerServices
    from api.app.jobs.runner import JobContext

ISSUES_KIND = "connector.jira.issues"
LINK_KIND = "atlassian"
LINK_TYPE = "jira_issue"
LOCK_PREFIX = "jira:"
NO_URL = "the Atlassian connector has no Jira URL (connectors.atlassian.jiraUrl)"
NO_PROJECT = "set the Jira project key in Settings → Connectors"
TRACKED_DECISIONS = frozenset({None, "fix"})
Outcome = Literal["ok", "failed", "refused"]


class Refused(Exception):
    """The job cannot run as asked and nothing was sent: audited ``refused``."""


class ItemsFailed(Exception):
    """Some items failed; each one was audited already."""


def bad_project_key(project_key: str) -> str | None:
    """Why ``project_key`` cannot be used, or ``None``."""
    if not project_key:
        return NO_PROJECT
    if not PROJECT_KEY.match(project_key):
        return f"the Jira project key {project_key!r} is not a project key (capital letters, digits, _)"
    return None


async def issue_source(session: AsyncSession, proc: Process) -> IssueSource:
    findings = (await session.execute(select(Finding).where(Finding.process_id == proc.id)
                                      .order_by(Finding.finding_key))).scalars()
    design = await latest_design(session, proc.id)
    questions = [dict(q) for q in ((design.record or {}).get("open_questions") or [])] if design else []
    diffs: dict[str, dict[str, Any]] = {}
    diff_ids: dict[str, str] = {}
    build = await latest_build(session, proc.id)
    if build is not None:
        runs = [r for r in await latest_verify_runs(session, build.id) if r.status == "differs"]
        if runs:
            scenarios = {s.id: s for s in (await session.execute(select(Scenario).where(
                Scenario.id.in_([r.scenario_id for r in runs])))).scalars()}
            run_scenario = {r.id: scenarios.get(r.scenario_id) for r in runs}
            for d in (await session.execute(select(FieldDiff).where(FieldDiff.run_id.in_(list(run_scenario)))
                                            .order_by(FieldDiff.id))).scalars():
                scenario = run_scenario[d.run_id]
                skey = scenario.key if scenario else d.run_id
                key = parity_key(skey, d.field)
                diff_ids[d.id] = key
                diffs[key] = {"scenario": scenario.name if scenario else skey, "scenario_key": skey,
                              "field": d.field, "kind": d.kind, "expected": d.expected, "actual": d.actual,
                              "classification": d.classification, "finding_key": d.finding_key, "note": d.note}
    return IssueSource(
        project_id=proc.project_id, process_id=proc.id, ir_id=proc.ir_id, name=proc.name,
        findings={f.finding_key: {"finding_key": f.finding_key, "severity": f.severity, "text": f.text,
                                  "decision": f.decision, "source": f.source, "citations": f.citations or [],
                                  "business_impact": f.business_impact, "note": f.note} for f in findings},
        questions=questions, diffs=diffs, diff_ids=diff_ids)


def check_item(src: IssueSource, item: IssueItem) -> IssueItem:
    """The item in its stable form; ``ValueError`` (API: 422) unless it exists and still has
    something to track. Messages name the item as asked."""
    stable = canonical(item, src)
    if item.type == "finding":
        decision = src.findings[item.ref]["decision"]
        if decision not in TRACKED_DECISIONS:
            raise ValueError(f"finding {item.ref} is decided {decision}: nothing to track")
    elif item.type == "question":
        decision = question_of(src, stable.ref).get("decision")
        if decision:
            raise ValueError(f"open question {item.ref} is {decision}: nothing to track")
    else:
        classification = src.diffs[stable.ref]["classification"]
        if classification != "defect":
            raise ValueError(f"parity difference {item.ref} is {classification}: nothing to track")
    return stable


def check_items(src: IssueSource, items: list[IssueItem]) -> list[IssueItem]:
    """Every item checked (``ValueError`` at the first bad one), in stable form, each once."""
    return list({i.local_ref: i for i in (check_item(src, item) for item in items)}.values())


def dedupe_key(process_id: str, items: list[IssueItem], create_again: bool) -> str:
    """The same request for the same process while one is still queued is not queued twice."""
    body = json.dumps([[i.type, i.ref] for i in items] + [create_again])
    return f"{LOCK_PREFIX}{process_id}:{hashlib.sha256(body.encode()).hexdigest()[:24]}"


def _secrets(atl: AtlassianConnector | None) -> list[str]:
    if atl is None:
        return []
    try:
        return header_secrets(atl.auth_header())
    except ConnectorNotConfigured:
        return []


class _Run:
    """One job's shared state: where to audit, the connector, the outcome lists."""

    def __init__(self, ctx: JobContext, w: WorkerServices, actor: str, create_again: bool) -> None:
        self.ctx, self.w, self.actor, self.create_again = ctx, w, actor, create_again
        self.where: dict[str, Any] = {"project_id": None, "process_id": None}
        self.atl: AtlassianConnector | None = None
        self.created: list[str] = []
        self.updated: list[str] = []
        self.stale: list[str] = []
        self.failed: list[str] = []

    async def audit(self, op: str, outcome: Outcome, target: str, detail: dict[str, Any],
                    session: AsyncSession | None = None) -> None:
        if session is not None:
            await audit_connector(session, self.actor, "jira", op, target=target, outcome=outcome, detail=detail,
                                  **self.where)
            return
        async with self.w.sessionmaker() as s:
            await audit_connector(s, self.actor, "jira", op, target=target, outcome=outcome, detail=detail,
                                  **self.where)
            await s.commit()

    def redacted(self, exc: BaseException) -> str:
        return redact(str(exc) or type(exc).__name__, _secrets(self.atl))

    def result(self) -> dict[str, Any]:
        return {"created": self.created, "updated": self.updated, "stale": self.stale}


async def issues_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Every outcome is audited: ``refused`` (off, no credential, no URL or project key, an item
    with nothing to track, a stale link skipped), ``failed`` (an error, its redacted text, on the
    item it happened to) or ``ok`` per issue; a job with a refused or failed item fails."""
    p = ctx.payload
    run = _Run(ctx, w, p.get("actor") or "system", bool(p.get("create_again")))
    asked = [IssueItem.model_validate(i) for i in p.get("items") or []]
    try:
        async with w.sessionmaker() as s:
            proc = await s.get(Process, p["process_id"])
            if proc is None:
                raise Refused(f"process {p['process_id']} not found")
            run.where.update(project_id=proc.project_id, process_id=proc.id)
            src = await issue_source(s, proc)
            application = await s.get(Application, proc.application_id)
            atl = await connector_for(s, w.settings, "atlassian")
        if not isinstance(atl, AtlassianConnector):
            raise TypeError(f"the Atlassian connector is served by {type(atl).__name__}")
        run.atl = atl
        atl.ready()  # off or no credential since the request was accepted: before any request
        if not atl.config.url("jira_url"):
            raise Refused(NO_URL)
        app_layer = atl.config.app
        project_key = str(getattr(app_layer, "project_key", "") or "")
        problem = bad_project_key(project_key)
        if problem:
            raise Refused(problem)
        try:
            items = check_items(src, asked)
        except ValueError as exc:
            raise Refused(str(exc)) from None
        jira = atl.jira
    except (ConnectorDisabled, ConnectorNotConfigured, Refused) as exc:
        await run.audit("create", "refused", str(p["process_id"]),
                        {"items": [i.local_ref for i in asked], "message": str(exc)})
        raise
    except Exception as exc:
        await run.audit("create", "failed", str(p["process_id"]),
                        {"items": [i.local_ref for i in asked], "message": run.redacted(exc)})
        raise

    app_name = application.name if application else proc.module or "application"
    issue_type = str(getattr(app_layer, "issue_type", "") or "Task")
    labels = list(getattr(app_layer, "labels", []) or [])
    await ctx.stage("issues")
    stop: str | None = None
    for item in items:
        if stop is not None:
            run.failed.append(f"{item.local_ref}: not attempted ({stop})")
            await run.audit("create", "failed", item.local_ref,
                            {"local_ref": item.local_ref, "message": f"not attempted: {stop}"})
            continue
        fields = issue_fields(item, src, app_name, project_key=project_key, issue_type=issue_type, labels=labels)
        op = ["create"]  # the step an error belongs to
        try:
            async with advisory_lock(w.sessionmaker, f"{LOCK_PREFIX}{proc.id}:{item.local_ref}"):
                await _one(run, jira, item, fields, src, project_key, op)
        except Exception as exc:  # noqa: BLE001  one bad item is audited and reported; the rest still run
            message = run.redacted(exc)
            run.failed.append(f"{item.local_ref}: {message}")
            await run.audit(op[0], "failed", item.local_ref, {"local_ref": item.local_ref, "message": message})
            await ctx.log(f"{item.local_ref}: failed: {message}")
            if isinstance(exc, ConnectorError) and exc.status == 401:
                stop = "Jira refused the credential (401)"
    if run.failed:
        done = run.result()
        raise ItemsFailed(f"{len(run.failed)} of {len(items)} item(s) failed: {'; '.join(run.failed)} "
                          f"(created {done['created']}, updated {done['updated']}, stale {done['stale']})")
    return run.result()


async def _one(run: _Run, jira: JiraConnector, item: IssueItem, fields: dict[str, Any], src: IssueSource,
               project_key: str, op: list[str]) -> None:
    """One item, under its lock; appends to ``run``'s created, updated or stale. ``op[0]`` is the
    step under way (``create`` or ``update``), for the audit of an error."""
    w, ctx = run.w, run.ctx
    async with w.sessionmaker() as s:
        link = await find_link(s, kind=LINK_KIND, object_type=LINK_TYPE, process_id=src.process_id,
                               local_ref=item.local_ref)
        link_key, link_stale = (link.remote_id, link.stale) if link else (None, False)

    async def save(key: str, audit_op: str) -> None:
        url = jira.browse_url(key)
        async with w.sessionmaker() as s:
            await upsert_link(s, **run.where, kind=LINK_KIND, object_type=LINK_TYPE, local_ref=item.local_ref,
                              remote_id=key, remote_url=url, created_by=run.actor)
            await run.audit(audit_op, "ok", url, {"key": key, "local_ref": item.local_ref}, session=s)
            await s.commit()

    async def update(key: str) -> None:
        op[0] = "update"
        await jira.update(key, update_fields(fields))
        await save(key, "update")
        await ctx.log(f"{item.local_ref}: updated {key}")

    if link_key is not None and not link_stale:
        try:
            await update(link_key)
            run.updated.append(link_key)
            return
        except ConnectorError as exc:
            if exc.status != 404:
                raise
        async with w.sessionmaker() as s:  # the issue is gone: never silently made again
            gone = await find_link(s, kind=LINK_KIND, object_type=LINK_TYPE, process_id=src.process_id,
                                   local_ref=item.local_ref)
            if gone is not None:
                gone.stale = True
            await run.audit("update", "failed", jira.browse_url(link_key),
                            {"key": link_key, "local_ref": item.local_ref,
                             "message": f"{link_key} is gone (404): link marked stale"}, session=s)
            await s.commit()
        await ctx.log(f"{item.local_ref}: {link_key} is gone; the link is marked stale")
        if not run.create_again:
            run.stale.append(item.local_ref)
            return
    elif link_key is not None:  # stale
        if not run.create_again:
            await run.audit("update", "refused", jira.browse_url(link_key),
                            {"key": link_key, "local_ref": item.local_ref,
                             "message": "the link is stale: create again to replace the issue"})
            await ctx.log(f"{item.local_ref}: the link to {link_key} is stale; skipped (create again to replace it)")
            run.stale.append(item.local_ref)
            return
        op[0] = "update"
        if await jira.exists(link_key):
            await update(link_key)
            run.updated.append(link_key)
            return

    op[0] = "create"
    label = ref_label(src.process_id, item.local_ref)
    found = await jira.find_by_label(project_key, label)  # an earlier create whose answer was lost
    if found is not None:
        await update(found[0])
        await ctx.log(f"{item.local_ref}: adopted {found[0]}, which carries its label")
        run.created.append(found[0])
        return
    key, _url = await jira.create(fields, ref_label=label)
    await save(key, "create")
    await ctx.log(f"{item.local_ref}: created {key}")
    run.created.append(key)
