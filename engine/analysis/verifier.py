"""Citation verifier: the plain-code gate between AI output and the analyst.

Every citation a rule carries must be found, whitespace-normalised, in the IR
evidence the cited activity actually has for that kind. Rules with any failing
citation are marked ``unverified`` in place, including ``approved`` and ``edited``
ones: an analyst edit that breaks a quote is downgraded, so callers (the
API's rule-edit endpoint) must re-run verification after an edit. A rule an analyst already ``rejected`` keeps
that decision (its failures are still reported). A citation under three characters must equal a whole stored value
rather than appear inside one.

A rule an iteration proposed (``source`` ``"iteration <n>"``) is grounded in the iteration's
requirements, not in the IR (ruling X21): without citations it counts as requirement-backed, is
neither verified nor failed and keeps its status; any citation it does carry is verified as usual.
"""
from __future__ import annotations

from engine.analysis.evidence import MIN_CITATION_LEN, EvidenceIndex, normalize
from engine.analysis.models import Citation, CitationFailure, Rule, VerificationResult
from engine.ir.models import Process

ITERATION_SOURCE = "iteration "


def is_requirement_backed(rule: Rule) -> bool:
    return (rule.source or "").startswith(ITERATION_SOURCE)


def verify_citations(process: Process, rules: list[Rule]) -> VerificationResult:
    index = EvidenceIndex(process)
    verified = total = backed = 0
    failures: list[CitationFailure] = []
    for rule in rules:
        rule_failures: list[CitationFailure] = []
        if not rule.citations and is_requirement_backed(rule):
            backed += 1
            continue
        if not rule.citations:
            total += 1
            rule_failures.append(CitationFailure(
                rule_id=rule.id,
                citation=Citation(activity_id="", kind="config", text=""),
                reason="rule has no citations",
            ))
        for cit in rule.citations:
            total += 1
            if not normalize(cit.text):
                reason = "citation text is empty"
            elif not index.known(cit.activity_id):
                reason = f"unknown activity: {cit.activity_id!r}"
            elif not index.has(cit.activity_id, cit.kind, cit.text):
                reason = (
                    f"citation text too short to match as a quote (under {MIN_CITATION_LEN} "
                    f"characters it has to equal a whole value of {cit.activity_id!r})"
                    if len(normalize(cit.text)) < MIN_CITATION_LEN
                    else f"text not found in {cit.kind} of {cit.activity_id!r}"
                )
            else:
                verified += 1
                continue
            rule_failures.append(CitationFailure(rule_id=rule.id, citation=cit, reason=reason))
        if rule_failures and rule.status != "rejected":
            rule.status = "unverified"
        failures.extend(rule_failures)
    return VerificationResult(verified=verified, total=total, failures=failures, requirement_backed=backed)
