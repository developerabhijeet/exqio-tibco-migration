"""The ``connector.jira.issues`` job: create or update one Jira issue per selected item (a finding,
an open question, an unexplained parity difference) of a process; each issue is recorded as a
``jira_issue`` link (``local_ref = "<type>:<ref>"``) and audited ``connector jira create|update``.

- A live link is updated (summary and description). A 404 there marks the link stale.
- A stale link is skipped and reported ``stale``, unless ``create_again``: then the old issue is
  updated if it exists again, else a new one is created.
- No link: an issue carrying the item's label (a create whose answer was lost) is adopted, else
  the issue is created.

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
    issue_fields,
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


class Refused(Exception):
    """The job cannot run as asked and nothing was sent: audited ``refused``."""


async def issue_source(session: AsyncSession, proc: Process) -> IssueSource:
    findings = (await session.execute(select(Finding).where(Finding.process_id == proc.id)
                                      .order_by(Finding.finding_key))).scalars()
    design = await latest_design(session, proc.id)
    questions = [dict(q) for q in ((design.record or {}).get("open_questions") or [])] if design else []
    diffs: dict[str, dict[str, Any]] = {}
    build = await latest_build(session, proc.id)
    if build is not None:
        runs = [r for r in await latest_verify_runs(session, build.id) if r.status == "differs"]
        if runs:
            names = {s.id: s.name for s in (await session.execute(select(Scenario).where(
                Scenario.id.in_([r.scenario_id for r in runs])))).scalars()}
            scenario_of = {r.id: names.get(r.scenario_id, r.scenario_id) for r in runs}
            for d in (await session.execute(select(FieldDiff).where(FieldDiff.run_id.in_(list(scenario_of)))
                                            .order_by(FieldDiff.id))).scalars():
                diffs[d.id] = {"scenario": scenario_of[d.run_id], "field": d.field, "kind": d.kind,
                               "expected": d.expected, "actual": d.actual, "classification": d.classification,
                               "finding_key": d.finding_key, "note": d.note}
    return IssueSource(
        project_id=proc.project_id, process_id=proc.id, ir_id=proc.ir_id, name=proc.name,
        findings={f.finding_key: {"finding_key": f.finding_key, "severity": f.severity, "text": f.text,
                                  "decision": f.decision, "source": f.source, "citations": f.citations or [],
                                  "business_impact": f.business_impact, "note": f.note} for f in findings},
        questions=questions, diffs=diffs)


def check_item(src: IssueSource, item: IssueItem) -> None:
    """``ValueError`` (API: 422) unless the item exists and still has something to track."""
    if item.type == "finding":
        f = src.findings.get(item.ref)
        if f is None:
            raise ValueError(f"finding {item.ref} not found")
        if f["decision"] not in TRACKED_DECISIONS:
            raise ValueError(f"finding {item.ref} is decided {f['decision']}: nothing to track")
    elif item.type == "question":
        if not item.ref.isdigit() or int(item.ref) >= len(src.questions):
            raise ValueError(f"open question {item.ref} not found")
        decision = src.questions[int(item.ref)].get("decision")
        if decision:
            raise ValueError(f"open question {item.ref} is {decision}: nothing to track")
    else:
        d = src.diffs.get(item.ref)
        if d is None:
            raise ValueError(f"parity difference {item.ref} is not in the latest verify run")
        if d["classification"] != "defect":
            raise ValueError(f"parity difference {item.ref} is {d['classification']}: nothing to track")


def unique_items(items: list[IssueItem]) -> list[IssueItem]:
    return list({i.local_ref: i for i in items}.values())


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


async def issues_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Every outcome is audited: ``refused`` (off, no credential, no URL or project key, an item
    with nothing to track), ``failed`` (any error, its redacted text; on the item it happened to)
    or ``ok`` per issue; an error is then raised to fail the job."""
    p = ctx.payload
    actor = p.get("actor") or "system"
    create_again = bool(p.get("create_again"))
    items = unique_items([IssueItem.model_validate(i) for i in p.get("items") or []])
    where: dict[str, Any] = {"project_id": None, "process_id": None}
    state: dict[str, Any] = {"atl": None, "op": "create", "target": str(p["process_id"]),
                             "detail": {"items": [i.local_ref for i in items]}}

    async def audit(outcome: Literal["ok", "failed", "refused"], detail: dict[str, Any]) -> None:
        async with w.sessionmaker() as s:
            await audit_connector(s, actor, "jira", state["op"], target=state["target"], outcome=outcome,
                                  detail={**state["detail"], **detail}, **where)
            await s.commit()

    created: list[str] = []
    updated: list[str] = []
    stale: list[str] = []
    try:
        async with w.sessionmaker() as s:
            proc = await s.get(Process, p["process_id"])
            if proc is None:
                raise Refused(f"process {p['process_id']} not found")
            where.update(project_id=proc.project_id, process_id=proc.id)
            src = await issue_source(s, proc)
            application = await s.get(Application, proc.application_id)
            atl = await connector_for(s, w.settings, "atlassian")
        if not isinstance(atl, AtlassianConnector):
            raise TypeError(f"the Atlassian connector is served by {type(atl).__name__}")
        state["atl"] = atl
        atl.ready()  # off or no credential since the request was accepted: before any request
        if not atl.config.url("jira_url"):
            raise Refused(NO_URL)
        app_layer = atl.config.app
        project_key = str(getattr(app_layer, "project_key", "") or "")
        if not project_key:
            raise Refused(NO_PROJECT)
        if not PROJECT_KEY.match(project_key):
            raise Refused(f"the Jira project key {project_key!r} is not a project key")
        for item in items:
            try:
                check_item(src, item)
            except ValueError as exc:
                raise Refused(str(exc)) from None
        jira = atl.jira
        app_name = application.name if application else proc.module or "application"
        await ctx.stage("issues")
        for item in items:
            fields = issue_fields(item, src, app_name, project_key=project_key,
                                  issue_type=str(getattr(app_layer, "issue_type", "") or "Task"),
                                  labels=list(getattr(app_layer, "labels", []) or []))
            state["detail"] = {"local_ref": item.local_ref}
            state["target"] = item.local_ref
            async with advisory_lock(w.sessionmaker, f"{LOCK_PREFIX}{proc.id}:{item.local_ref}"):
                outcome = await _one(ctx, w, jira, item, fields, src, project_key, create_again, actor, where,
                                     audit, state)
            {"created": created, "updated": updated, "stale": stale}[outcome[0]].append(outcome[1])
        return {"created": created, "updated": updated, "stale": stale}
    except (ConnectorDisabled, ConnectorNotConfigured, Refused) as exc:
        await audit("refused", {"message": str(exc)})
        raise
    except Exception as exc:
        await audit("failed", {"message": redact(str(exc) or type(exc).__name__, _secrets(state["atl"]))})
        raise


async def _one(ctx: JobContext, w: WorkerServices, jira: JiraConnector, item: IssueItem, fields: dict[str, Any],
               src: IssueSource, project_key: str, create_again: bool, actor: str, where: dict[str, Any],
               audit: Any, state: dict[str, Any]) -> tuple[str, str]:
    """One item, under its lock: ``("created"|"updated", key)`` or ``("stale", local ref)``."""
    async with w.sessionmaker() as s:
        link = await find_link(s, kind=LINK_KIND, object_type=LINK_TYPE, process_id=src.process_id,
                               local_ref=item.local_ref)
        link_key, link_stale = (link.remote_id, link.stale) if link else (None, False)

    async def save(key: str, url: str) -> None:
        async with w.sessionmaker() as s:
            await upsert_link(s, **where, kind=LINK_KIND, object_type=LINK_TYPE, local_ref=item.local_ref,
                              remote_id=key, remote_url=url, created_by=actor)
            await audit_connector(s, actor, "jira", state["op"], target=url, outcome="ok",
                                  detail={"key": key, "local_ref": item.local_ref}, **where)
            await s.commit()

    async def update(key: str) -> None:
        state["op"], state["target"] = "update", jira.browse_url(key)
        await jira.update(key, update_fields(fields))
        await save(key, jira.browse_url(key))
        await ctx.log(f"{item.local_ref}: updated {key}")

    if link_key is not None and not link_stale:
        try:
            await update(link_key)
            return "updated", link_key
        except ConnectorError as exc:
            if exc.status != 404:
                raise
        async with w.sessionmaker() as s:  # the issue is gone: never silently made again
            gone = await find_link(s, kind=LINK_KIND, object_type=LINK_TYPE, process_id=src.process_id,
                                   local_ref=item.local_ref)
            if gone is not None:
                gone.stale = True
            await audit_connector(s, actor, "jira", "update", target=jira.browse_url(link_key), outcome="failed",
                                  detail={"key": link_key, "local_ref": item.local_ref,
                                          "message": f"{link_key} is gone (404): link marked stale"}, **where)
            await s.commit()
        await ctx.log(f"{item.local_ref}: {link_key} is gone; the link is marked stale")
        if not create_again:
            return "stale", item.local_ref
    elif link_key is not None:  # stale
        if not create_again:
            await ctx.log(f"{item.local_ref}: the link to {link_key} is stale; skipped (create again to replace it)")
            return "stale", item.local_ref
        if await jira.exists(link_key):
            await update(link_key)
            return "updated", link_key

    state["op"], state["target"] = "create", item.local_ref
    label = ref_label(src.process_id, item.local_ref)
    found = await jira.find_by_label(project_key, label)  # an earlier create whose answer was lost
    if found is not None:
        await update(found[0])
        state["op"] = "create"
        await ctx.log(f"{item.local_ref}: adopted {found[0]}, created by an earlier run")
        return "created", found[0]
    key, url = await jira.create(fields, ref_label=label)
    await save(key, url)
    await ctx.log(f"{item.local_ref}: created {key}")
    return "created", key
