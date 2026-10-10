"""Scripted model replies for ``PROVIDER=fake`` (tests, offline demos).

Every reply is built at request time from the process IR or from the prompt itself, never from
hard-coded line numbers, so the citations quote real IR text and pass the citation verifier.
The synthetic estate's DealerFundingRequest and CreditApplicationIntake get business wording;
any other process gets generic, still verifiable, rules.

Dispatch is on the request tags the engine agents set: ``stage`` ``analyse`` (Workflow
Analyst), ``risk`` (Risk Reviewer), ``challenge`` (Independent Challenger), ``name`` (domain
namer), and ``feature`` ``ask`` (Ask).
"""
from __future__ import annotations

import json
import re

from engine.agents.provider import FakeProvider, LLMRequest, LLMResponse
from engine.analysis.coverage import coverage_matrix
from engine.analysis.models import Citation, Rule
from engine.ir.models import Process

# (process name, source activity of the first conditional transition) -> business wording
BUSINESS_RULES: dict[tuple[str, str], str] = {
    ("DealerFundingRequest", "LookupDealer"):
        "A funding request proceeds only when the dealer's status is ACTIVE; any other status "
        "raises DealerInactiveFault and nothing is funded.",
    ("DealerFundingRequest", "LookupDeal"):
        "The requested funding amount must lie within the tolerance of the deal's approved "
        "amount; otherwise a REJECTED funding event with reason AMOUNT_OUTSIDE_TOLERANCE is "
        "published.",
    ("DealerFundingRequest", "CheckExistingFunding"):
        "A deal is funded at most once: funding is inserted only when no funding record exists "
        "for the deal, and a repeat request is logged as DF-409 and ignored.",
    ("CreditApplicationIntake", "CallAuditLog"):
        "An application continues only when the applicant SSN has the form NNN-NN-NNNN and does "
        "not start with 000 or 666; otherwise the caller receives 400.",
    ("CreditApplicationIntake", "CallValidateVIN"):
        "The vehicle VIN must be confirmed valid by ValidateVIN; otherwise the caller receives "
        "400 with the rejection reason.",
    ("CreditApplicationIntake", "CheckDuplicateApplication"):
        "An applicant may have only one application in flight: when a prior application exists "
        "for the same SSN, DuplicateApplicationFault is raised and the caller receives 409.",
}

SUMMARIES: dict[str, str] = {
    "DealerFundingRequest":
        "DealerFundingRequest consumes funding requests from a JMS queue, checks that the dealer "
        "is active and that the amount is within tolerance of the approved deal, funds each deal "
        "at most once and publishes a FUNDED or REJECTED funding event.",
    "CreditApplicationIntake":
        "CreditApplicationIntake receives credit applications over REST, normalizes the "
        "applicant, validates the SSN and the VIN, refuses duplicate applications, pulls bureau "
        "scores and returns the decision of the DecisionEngine.",
}

DOMAIN_NAMES: dict[str, str] = {
    "Credit Origination": "Retail Credit Origination",
    "Dealer Funding": "Dealer Funding and Disbursement",
    "Loan Servicing": "Loan Servicing and Collections",
    "Legacy Address": "Customer Address Standardization",
}

IMPACTS: dict[str, str] = {
    "S1": "Two requests for the same deal can both pass the check and both write, so a deal "
          "could be funded twice and money leaves the business without control.",
    "S2": "A wildcard match on an identifier can pick the wrong record, so a request may be "
          "processed against another customer's or dealer's data.",
    "S3": "A hard-coded endpoint breaks when the service moves environment and hides a "
          "dependency the migration has to carry over.",
    "S4": "A response field the contract promises is never filled, so consumers receive an empty "
          "value they may rely on.",
    "S5": "Cases that match no condition stop silently, so the caller gets no answer for them.",
    "S6": "Errors are caught and dropped, so failures are invisible to operations.",
    "S7": "Regulated personal data is written in clear text, which widens the audit and breach "
          "scope of the service.",
}

TECHNICAL_REASON = "Plumbing step with no business decision of its own (data mapping or wiring)."
_SQL_VERB = {"select": "reads", "insert": "inserts into", "update": "updates", "delete": "deletes from"}


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def _sql(config: dict) -> str | None:
    settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    for value in (config.get("sql"), settings.get("sqlStatement"), settings.get("statement")):
        if isinstance(value, str) and value.strip():
            return value
    return None


def analyst_rules(process: Process) -> list[dict]:
    names = {a.id: a.name for a in process.activities}
    rules: list[dict] = []
    seen_sources: set[str] = set()
    for t in process.transitions:
        if t.kind != "conditional" or not t.expression or len(t.expression.strip()) < 3:
            continue
        text = None
        if t.from_id not in seen_sources:
            text = BUSINESS_RULES.get((process.name, t.from_id))
        seen_sources.add(t.from_id)
        if text is None:
            text = (f"After {names.get(t.from_id, t.from_id)} the flow continues to "
                    f"{names.get(t.to_id, t.to_id)} only when the condition holds; other cases "
                    "take the alternative path.")
        rules.append({"text": text, "confidence": 0.9 if (process.name, t.from_id) in BUSINESS_RULES
                      else 0.7,
                      "citations": [{"activity_id": t.from_id, "kind": "condition",
                                     "text": t.expression}]})
    for a in process.activities:
        sql = _sql(a.config)
        if not sql:
            continue
        line = _first_line(sql)
        if len(line) < 3:
            continue
        verb = _SQL_VERB.get(line.split()[0].lower(), "uses")
        rules.append({"text": f"{a.name} {verb} the database with the statement '{line}'.",
                      "confidence": 0.8,
                      "citations": [{"activity_id": a.id, "kind": "sql", "text": line}]})
    return rules


def analyst_reply(process: Process) -> dict:
    rules = analyst_rules(process)
    engine_rules = [Rule(id=f"R{i}", text=r["text"], confidence=r["confidence"],
                         citations=[Citation(**c) for c in r["citations"]])
                    for i, r in enumerate(rules, 1)]
    uncovered = [i for i in coverage_matrix(process, engine_rules, {}).items if not i.covered_by]
    summary = SUMMARIES.get(process.name) or (
        f"{process.name} is a {process.dialect} process"
        f"{f' started by a {process.starter.kind} trigger' if process.starter else ''} with "
        f"{len(process.activities)} activities and {len(process.transitions)} transitions.")
    return {"summary": summary, "rules": rules,
            "technical": [{"ref": i.ref, "reason": TECHNICAL_REASON} for i in uncovered]}


def risk_reply(process: Process | None, user: str) -> dict:
    ids = re.findall(r"^- id=(\S+) severity=", user, flags=re.MULTILINE)
    impacts = [{"finding_id": fid, "business_impact": IMPACTS.get(fid.split("-")[0],
                                                                  "Review with the business owner.")}
               for fid in ids]
    findings: list[dict] = []
    if process is not None:
        sources_with_otherwise = {t.from_id for t in process.transitions if t.kind == "otherwise"}
        names = {a.id: a.name for a in process.activities}
        candidates = [t for t in process.transitions
                      if t.kind == "conditional" and t.expression and t.from_id in sources_with_otherwise]
        candidates.sort(key=lambda t: "abs(" not in t.expression)  # a tolerance test first
        for t in candidates[:1]:
            where = names.get(t.from_id, t.from_id)
            if "abs(" in t.expression:
                findings.append({
                    "severity": "medium",
                    "text": (f"The tolerance test after {where} accepts a difference exactly equal to "
                             "the tolerance (<=). Confirm the boundary with the funding policy."),
                    "citations": [{"activity_id": t.from_id, "kind": "condition", "text": t.expression}],
                    "business_impact": "If policy intends a strict limit, requests at the boundary "
                                       "are funded when they should be rejected.",
                })
            else:
                findings.append({
                    "severity": "low",
                    "text": (f"The condition after {where} is the only test on this path: every other "
                             "case falls through to the otherwise branch. Confirm with the business "
                             "that no other outcome is intended."),
                    "citations": [{"activity_id": t.from_id, "kind": "condition", "text": t.expression}],
                    "business_impact": "A case the business expects to be handled differently "
                                       "would be processed by the fallback path without notice.",
                })
    return {"impacts": impacts, "findings": findings}


def challenger_reply(user: str) -> dict:
    verdicts = []
    for rid, text in re.findall(r"^- id=(\S+): (.*)$", user, flags=re.MULTILINE):
        if "tolerance" in text:
            verdicts.append({"rule_id": rid, "verdict": "disagree",
                             "reason": "The condition compares with <=, so an amount exactly at the "
                                       "tolerance is accepted; confirm that boundary with the business."})
        else:
            verdicts.append({"rule_id": rid, "verdict": "agree",
                             "reason": "The cited source supports the rule as written."})
    return {"verdicts": verdicts}


def namer_reply(user: str) -> dict:
    out = []
    for did, current in re.findall(r"^id=(\S+) current_name=(.*?) processes=", user, flags=re.MULTILINE):
        out.append({"id": did, "name": DOMAIN_NAMES.get(current.strip(), current.strip())})
    return {"domains": out}


def ask_reply(user: str) -> dict:
    hits = re.findall(r"^\[id=(\S+) kind=(\S+) origin=[^\]]*\] (.*)$", user, flags=re.MULTILINE)
    if not hits:
        return {"answer": "The knowledge base does not cover this question.", "citation_ids": []}
    top = hits[:3]
    answer = "From the knowledge base: " + " ".join(text.strip()[:300] for _, _, text in top)
    return {"answer": answer, "citation_ids": [hid for hid, _, _ in top]}


def reply_for(req: LLMRequest, process: Process | None) -> str:
    user = "".join(str(m.get("content", "")) for m in req.messages if m.get("role") == "user")
    stage = req.tags.get("stage")
    if req.tags.get("feature") == "ask":
        data = ask_reply(user)
    elif stage == "analyse" and process is not None:
        data = analyst_reply(process)
    elif stage == "risk":
        data = risk_reply(process, user)
    elif stage == "challenge":
        data = challenger_reply(user)
    elif stage == "name":
        data = namer_reply(user)
    else:
        data = {}
    return json.dumps(data)


class ScriptedFakeProvider(FakeProvider):
    """A ``FakeProvider`` whose next scripted reply is computed from the request."""

    def __init__(self, process: Process | None = None) -> None:
        super().__init__([], name="fake")
        self.process = process

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self._responses.append(reply_for(req, self.process))
        return await super().complete(req)
