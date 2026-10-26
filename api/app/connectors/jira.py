"""Jira: issues from findings, open questions and parity differences (spec section 5).

Both flavors use REST v2 (``/rest/api/2/issue``, ``/rest/api/2/myself``) with wiki-markup
descriptions; v3's ADF is Cloud only and v2 is still served there. Search is
``/rest/api/2/search`` on Data Center and ``/rest/api/2/search/jql`` on Cloud, where Atlassian
removed the old endpoint.

Issue text is built by code from stored rows only. Every value is masked (``mask_sensitive`` plus
the password, secret and token fields) and then escaped for wiki markup, so ``{code}``,
``[link|url]``, ``!image!`` or ``[~user]`` in a finding shows as text. The summary is plain text,
so it is masked but not escaped.

Every issue carries a label derived from its process and local item (``ref_label``). A create
whose answer is lost (a timeout, a 5xx: Jira may have stored the issue) is never posted again:
the label is searched, and an issue found there is adopted. When nothing is found yet, the create
is reported as failed, and the next run searches again before creating.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from api.app.connectors.base import ConnectionTest, ConnectorError
from api.app.connectors.confluence_page import PAGE_MASKED_FIELDS
from engine.agents.provider import mask_sensitive

if TYPE_CHECKING:
    from api.app.connectors.registry import AtlassianConnector

ItemType = Literal["finding", "question", "parity"]
SUMMARY_MAX = 255
PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
TYPE_LABELS: dict[str, str] = {"finding": "finding", "question": "open-question", "parity": "parity"}
JSON = {"Accept": "application/json"}
#: Wiki-markup characters escaped with a backslash; a backslash itself becomes an entity, since a
#: doubled one is a forced line break and a single one would escape the next character.
_WIKI_SPECIAL = re.compile(r"([{}\[\]|*_\-+^~!])")


class IssueItem(BaseModel):
    type: ItemType
    ref: str = Field(min_length=1, max_length=200)  # finding key, question index, field diff id

    @property
    def local_ref(self) -> str:
        return f"{self.type}:{self.ref}"


class IssueSource(BaseModel):
    """What a process's issues are made of, read from the database (``services.jira_issues``):
    findings by key, the latest design's open questions, and the unexplained differences of the
    latest verify run of the latest finished build by field diff id."""

    project_id: str
    process_id: str
    ir_id: str
    name: str
    findings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    diffs: dict[str, dict[str, Any]] = Field(default_factory=dict)


def wiki_escape(text: str) -> str:
    """``text`` shown as itself in Jira wiki markup."""
    return _WIKI_SPECIAL.sub(r"\\\1", text.replace("\\", "&#92;"))


def _mask(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return mask_sensitive(str(value), PAGE_MASKED_FIELDS)


def _wiki(value: Any) -> str:
    return wiki_escape(_mask(value).replace("\r\n", "\n").replace("\r", "\n"))


def _summary(text: str) -> str:
    one = " ".join(_mask(text).split())
    return one if len(one) <= SUMMARY_MAX else one[:SUMMARY_MAX - 1].rstrip() + "…"


def _label(value: str) -> str:
    return "-".join(str(value).split())


def ref_label(process_id: str, local_ref: str) -> str:
    """The label that finds this item's issue again (the same on every run)."""
    return "exiqo-ref-" + hashlib.sha256(f"{process_id}\n{local_ref}".encode()).hexdigest()[:20]


def _title(text: str) -> str:
    first = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
    return first or "finding"


def _citations(citations: list[dict[str, Any]]) -> list[str]:
    if not citations:
        return []
    out = ["", "*TIBCO citations*"]
    for c in citations:
        out.append(f"* activity {_wiki(c.get('activity_id'))}, {_wiki(c.get('kind'))}: {_wiki(c.get('text'))}")
    return out


def _finding(item: IssueItem, src: IssueSource) -> tuple[str, list[str], list[str]]:
    f = src.findings.get(item.ref)
    if f is None:
        raise KeyError(f"finding {item.ref} not found")
    lines = [(f"*Finding {_wiki(item.ref)}* (severity {_wiki(f.get('severity'))}, "
              f"decision {_wiki(f.get('decision') or 'undecided')}, source {_wiki(f.get('source'))})"), "",
             _wiki(f.get("text"))]
    if f.get("business_impact"):
        lines += ["", f"*Business impact:* {_wiki(f['business_impact'])}"]
    if f.get("note"):
        lines += ["", f"*Note:* {_wiki(f['note'])}"]
    lines += _citations(f.get("citations") or [])
    labels = [TYPE_LABELS["finding"], *([_label(f["severity"])] if f.get("severity") else [])]
    return _title(f.get("text") or ""), lines, labels


def _question(item: IssueItem, src: IssueSource) -> tuple[str, list[str], list[str]]:
    q = _question_at(src, item.ref)
    lines = [f"*Open question {_wiki(item.ref)}*", "", _wiki(q.get("question")), "",
             f"*Recommendation:* {_wiki(q.get('recommendation'))}"]
    return f"Open question: {q.get('question') or ''}", lines, [TYPE_LABELS["question"]]


def _question_at(src: IssueSource, ref: str) -> dict[str, Any]:
    if not ref.isdigit() or int(ref) >= len(src.questions):
        raise KeyError(f"open question {ref} not found")
    return src.questions[int(ref)]


def _parity(item: IssueItem, src: IssueSource) -> tuple[str, list[str], list[str]]:
    d = src.diffs.get(item.ref)
    if d is None:
        raise KeyError(f"parity difference {item.ref} is not in the latest verify run")
    lines = [(f"*Parity difference* in scenario {_wiki(d.get('scenario'))}, {_wiki(d.get('kind') or 'field')} "
              f"{_wiki(d.get('field'))} ({_wiki(d.get('classification'))})"), "",
             f"*Expected (TIBCO):* {_wiki(d.get('expected'))}", f"*Actual (migrated):* {_wiki(d.get('actual'))}"]
    if d.get("finding_key"):
        lines.append(f"*Finding:* {_wiki(d['finding_key'])}")
    if d.get("note"):
        lines += ["", f"*Note:* {_wiki(d['note'])}"]
    return f"Parity: {d.get('scenario') or ''} {d.get('field') or ''}", lines, [TYPE_LABELS["parity"]]


_BUILDERS = {"finding": _finding, "question": _question, "parity": _parity}


def issue_fields(item: IssueItem, process: IssueSource, app: str, *, project_key: str, issue_type: str,
                 labels: list[str]) -> dict[str, Any]:
    """The create body (``{"fields": ...}``) of ``item``'s issue; ``KeyError`` for an unknown item.
    The description ends with the back-reference line ``ExiQO: project <id>, process <ir id>,
    <type> <ref>``."""
    title, lines, own_labels = _BUILDERS[item.type](item, process)
    back = (f"ExiQO: project {_wiki(process.project_id)}, process {_wiki(process.ir_id)}, "
            f"{item.type} {_wiki(item.ref)}")
    description = "\n".join([*lines, "", f"Application {_wiki(app)}, process {_wiki(process.name)}.", "", back])
    all_labels = list(dict.fromkeys(
        [_label(x) for x in labels if str(x).strip()] + own_labels + [ref_label(process.process_id, item.local_ref)]))
    return {"fields": {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
        "summary": _summary(f"[{process.name}] {title}"),
        "description": description,
        "labels": all_labels,
    }}


def update_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """What a re-publish changes: summary and description (never project, type or labels)."""
    f = fields["fields"]
    return {"fields": {"summary": f["summary"], "description": f["description"]}}


def _ambiguous(exc: ConnectorError) -> bool:
    """The create may have happened: no answer (timeout, reset) or a server error."""
    return exc.status is None or exc.status >= 500


class JiraConnector:
    """Jira REST v2 over the Atlassian connector's allowlisting client."""

    def __init__(self, connector: AtlassianConnector) -> None:
        self.connector = connector
        self.base_url = connector.config.url("jira_url")
        if not self.base_url:
            raise ConnectorError("the Atlassian connector has no Jira URL (connectors.atlassian.jiraUrl)")
        self.client = connector.client(self.base_url)
        self.flavor = connector.flavor

    def browse_url(self, key: str) -> str:
        return f"{self.base_url}/browse/{key}"

    async def test(self) -> ConnectionTest:
        self.connector.ready()
        me = await self.client.json("GET", "/rest/api/2/myself", headers=JSON) or {}
        message = f"Jira {self.flavor}"
        ok = True
        project = getattr(self.connector.config.app, "project_key", "")
        if project:
            try:
                await self.client.json("GET", f"/rest/api/2/project/{quote(project, safe='')}", headers=JSON)
                message += f", project {project} found"
            except ConnectorError as exc:
                if exc.status != 404:
                    raise
                ok, message = False, f"{message}, project {project} not found"
        return ConnectionTest(ok=ok, account=me.get("displayName") or me.get("name"), site=self.base_url,
                              message=message)

    async def create(self, fields: dict[str, Any], *, ref_label: str | None = None) -> tuple[str, str]:
        """Create the issue; ``(key, url)``. A lost answer is resolved through ``ref_label``."""
        try:
            out = await self.client.json("POST", "/rest/api/2/issue", json=fields, headers=JSON) or {}
        except ConnectorError as exc:
            if ref_label is None or not _ambiguous(exc):
                raise
            project = fields["fields"]["project"]["key"]
            found = await self.find_by_label(project, ref_label)
            if found is not None:
                return found
            raise ConnectorError(f"Jira did not confirm the create ({exc.message}); the issue may have been "
                                 f"created: run again, it is looked up by its label {ref_label} first",
                                 exc.status) from None
        key = str(out.get("key") or "")
        if not key:
            raise ConnectorError("Jira answered the create without an issue key")
        return key, self.browse_url(key)

    async def update(self, key: str, fields: dict[str, Any]) -> None:
        await self.client.json("PUT", f"/rest/api/2/issue/{quote(key, safe='')}", json=fields, headers=JSON)

    async def exists(self, key: str) -> bool:
        try:
            await self.client.json("GET", f"/rest/api/2/issue/{quote(key, safe='')}",
                                   params={"fields": "summary"}, headers=JSON)
        except ConnectorError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    async def find_by_label(self, project_key: str, label: str) -> tuple[str, str] | None:
        """The oldest issue of ``project_key`` carrying ``label``, or ``None``."""
        if not PROJECT_KEY.match(project_key) or not re.fullmatch(r"[A-Za-z0-9_.\-]+", label):
            raise ValueError(f"not a Jira project key and label: {project_key!r} {label!r}")
        path = "/rest/api/2/search/jql" if self.flavor == "cloud" else "/rest/api/2/search"
        jql = f'project = "{project_key}" AND labels = "{label}" ORDER BY created ASC'
        out = await self.client.json("GET", path, params={"jql": jql, "fields": "summary", "maxResults": 1},
                                     headers=JSON) or {}
        issues = out.get("issues") or []
        if not issues:
            return None
        key = str(issues[0].get("key") or "")
        return (key, self.browse_url(key)) if key else None
