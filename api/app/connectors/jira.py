"""Jira: issues from findings, open questions and parity differences (spec section 5).

Both flavors use REST v2 (``/rest/api/2/issue``, ``/rest/api/2/myself``) with wiki-markup
descriptions; v3's ADF is Cloud only and v2 is still served there. Search is
``/rest/api/2/search`` on Data Center and ``/rest/api/2/search/jql`` on Cloud, where Atlassian
removed the old endpoint.

Identity (ruling X17): an item's ``local_ref`` and label never depend on a list position or a
per-run id. A finding is ``finding:<finding key>``; an open question is
``question:<sha256(normalised question text)[:16]>``, so a re-design that reorders the questions
still updates the right issue; a parity difference is ``parity:<scenario key>:<field path>``, so
the same difference found by a re-verify maps to the same issue. A request names a question by
its id (``question_id``, which the design read API returns with each question; a list position
is refused) and a difference by its key or its field diff id, which ``canonical`` turns into the
stable form against the current data.

Issue text is built by code from stored rows only. Every value is masked (``mask_sensitive`` plus
the password, secret and token fields) and then escaped for wiki markup: ``{code}``,
``[link|url]``, ``!image!``, ``[~user]``, line-start ``#``, ``h1.``-``h6.`` and ``bq.``, and the
emoticon tokens (``:)``, ``(y)``...) show as text. Bare URLs (``http://...``) are still turned into
links by Jira's renderer: wiki markup has no escape for them (an explicit link or image needs
``[`` or ``!``, which are escaped). The summary is plain text, so it is masked but not escaped.
A description longer than Jira's 32,767 characters is cut, keeping the back-reference line.

Every issue carries a label derived from its process and identity (``ref_label``). A create whose
answer is lost (a timeout, a 5xx: Jira may have stored the issue) is never posted again: the label
is searched, and an issue found there is adopted. When nothing is found yet, the create is
reported as failed, and the next run searches again before creating. Adoption trusts the label:
an issue someone cloned in Jira keeps its labels, so a clone of an ExiQO issue (or an issue
labelled by hand) can be adopted for an item that has no link; the oldest issue with the label
wins.
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
DESCRIPTION_MAX = 32_000  # Jira refuses more than 32,767 characters
TRUNCATED = "… (cut to fit Jira; the full text is in ExiQO)"
PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
TYPE_LABELS: dict[str, str] = {"finding": "finding", "question": "open-question", "parity": "parity"}
JSON = {"Accept": "application/json"}
_QUESTION_ID = re.compile(r"^[0-9a-f]{16}$")
#: One pass over the text (the output is never rescanned). A backslash becomes ``&#92;`` (doubled
#: it is a forced line break, single it escapes the next character); the markup characters get a
#: backslash; so does the face of ``:) :( :P :D ;)`` and the ``(`` of ``(y) (n) (i) (/) (x) (?) (on)
#: (off) (flag) (flagoff)`` (the other emoticons hold an escaped ``! + - *``). An entity is never
#: followed by a bare face character, or its ``;`` would make an emoticon.
_WIKI_TOKEN = re.compile(r"(?P<bs>\\)(?P<after>[()PDp])?|(?P<smile>[:;])(?P<face>[()PDp])"
                         r"|(?P<paren>\()(?=(?:y|n|i|/|x|\?|on|off|flag|flagoff)\))"
                         r"|(?P<special>[{}\[\]|*_\-+^~!])")
_LINE_START = re.compile(r"(?m)^([ \t]*)(#|h[1-6](?=\.)|bq(?=\.))")


class IssueItem(BaseModel):
    type: ItemType
    # finding key; question id (never its index); ``<scenario key>:<field path>`` or field diff id
    ref: str = Field(min_length=1, max_length=500)

    @property
    def local_ref(self) -> str:
        return f"{self.type}:{self.ref}"


class IssueSource(BaseModel):
    """What a process's issues are made of, read from the database (``services.jira_issues``):
    findings by key, the latest design's open questions (in order), and the unexplained
    differences of the latest verify run of the latest finished build by
    ``<scenario key>:<field path>`` (``diff_ids`` maps a field diff id to that key)."""

    project_id: str
    process_id: str
    ir_id: str
    name: str
    findings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    diffs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    diff_ids: dict[str, str] = Field(default_factory=dict)


def question_id(text: str) -> str:
    """The identity of an open question: its text, whitespace collapsed and case folded."""
    return hashlib.sha256(" ".join(str(text or "").split()).casefold().encode()).hexdigest()[:16]


def parity_key(scenario_key: str, field: str) -> str:
    return f"{scenario_key}:{field}"


def canonical(item: IssueItem, src: IssueSource) -> IssueItem:
    """``item`` with its stable ref (X17); ``ValueError`` when ``src`` has no such item."""
    if item.type == "finding":
        if item.ref not in src.findings:
            raise ValueError(f"finding {item.ref} not found")
        return item
    if item.type == "question":
        ids = [question_id(q.get("question") or "") for q in src.questions]
        if _QUESTION_ID.match(item.ref) and item.ref in ids:
            return item
        if item.ref.isdigit() and not _QUESTION_ID.match(item.ref):
            raise ValueError(f"open question {item.ref}: name it by its question_id, not its position")
        raise ValueError(f"open question {item.ref} not found")
    if item.ref in src.diffs:
        return item
    if item.ref in src.diff_ids:
        return IssueItem(type="parity", ref=src.diff_ids[item.ref])
    raise ValueError(f"parity difference {item.ref} is not in the latest verify run")


def question_of(src: IssueSource, qid: str) -> dict[str, Any]:
    return next(q for q in src.questions if question_id(q.get("question") or "") == qid)


def _escape(m: re.Match[str]) -> str:
    if m.group("bs"):
        return "&#92;" + ("\\" + m.group("after") if m.group("after") else "")
    if m.group("smile"):
        return m.group("smile") + "\\" + m.group("face")
    return "\\" + m.group(0)


def wiki_escape(text: str) -> str:
    """``text`` shown as itself in Jira wiki markup."""
    out = _WIKI_TOKEN.sub(_escape, text)
    return _LINE_START.sub(lambda m: m.group(1) + (r"\#" if m.group(2) == "#" else m.group(2) + "\\"), out)


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


def _cut(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters without splitting an escape (``\\x`` or ``&#nn;``)."""
    if len(text) <= limit:
        return text
    out = text[:limit]
    amp = out.rfind("&", max(0, len(out) - 6))
    if amp != -1 and ";" not in out[amp:]:
        out = out[:amp]
    trailing = len(out) - len(out.rstrip("\\"))
    if trailing % 2:
        out = out[:-1]
    return out


def _description(body: str, back: str) -> str:
    tail = "\n\n" + back
    if len(body) + len(tail) <= DESCRIPTION_MAX:
        return body + tail
    room = DESCRIPTION_MAX - len(tail) - len(TRUNCATED) - 2
    return _cut(body, room) + "\n\n" + TRUNCATED + tail


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
    f = src.findings[item.ref]
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
    q = question_of(src, item.ref)
    lines = ["*Open question*", "", _wiki(q.get("question")), "",
             f"*Recommendation:* {_wiki(q.get('recommendation'))}"]
    return f"Open question: {q.get('question') or ''}", lines, [TYPE_LABELS["question"]]


def _parity(item: IssueItem, src: IssueSource) -> tuple[str, list[str], list[str]]:
    d = src.diffs[item.ref]
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
    """The create body (``{"fields": ...}``) of ``item``'s issue (its stable identity:
    ``canonical``); ``ValueError`` for an unknown item. The description ends with the
    back-reference line ``ExiQO: project <id>, process <ir id>, <type> <ref>``."""
    item = canonical(item, process)
    title, lines, own_labels = _BUILDERS[item.type](item, process)
    back = (f"ExiQO: project {_wiki(process.project_id)}, process {_wiki(process.ir_id)}, "
            f"{item.type} {_wiki(item.ref)}")
    body = "\n".join([*lines, "", f"Application {_wiki(app)}, process {_wiki(process.name)}."])
    all_labels = list(dict.fromkeys(
        [_label(x) for x in labels if str(x).strip()] + own_labels + [ref_label(process.process_id, item.local_ref)]))
    return {"fields": {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
        "summary": _summary(f"[{process.name}] {title}"),
        "description": _description(body, back),
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
        return f"{self.base_url}/browse/{quote(key, safe='')}"

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
