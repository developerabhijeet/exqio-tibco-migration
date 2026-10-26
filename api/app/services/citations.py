"""Inline citation spans (``cited``) for the Discover domains and the Validate rules and findings.

Each claim keeps every citation it carries: a ref anchors on the first sentence naming its
activity (or an identifier its quote holds), else on the last sentence (``engine.citations.claims``).
Text is masked before anchoring, so offsets stay true to the text shown."""
from __future__ import annotations

import re
from typing import Any

from engine.agents.provider import mask_sensitive
from engine.citations.claims import HintedRef, anchor, tail
from engine.citations.model import (
    LABEL_MAX,
    PREVIEW_MAX,
    CitedText,
    CiteRef,
    CiteTarget,
    UiPhase,
    cut,
)
from engine.citations.uids import activity_uid

_WORD = re.compile(r"[A-Za-z_][\w]*")
# An identifier, not a common word: snake case, a digit, or a capital after the first letter.
_IDENTIFIER = re.compile(r"_|\d|.[A-Z]")

# Queues and topics read as one sentence.
_SAME_SENTENCE = {"topic": "queue"}
_DOMAIN_SENTENCE = {
    "table": "Shares the tables {}.",
    "queue": "Uses the queues {}.",
    "call": "Calls between {}.",
    "module": "Lives in the modules {}.",
    "name": "Named alike: {}.",
}


def _identifier_like(name: str) -> bool:
    """Multi-word names and identifiers anchor; a single common word (``Map``, ``Check``)
    would anchor on any prose that uses the word, so it does not."""
    name = name.strip()
    return bool(name) and (len(name.split()) > 1 or bool(_IDENTIFIER.search(name)))


def hints_for(activity_id: str | None, activity_name: str | None, quote: str | None) -> list[str]:
    """The names that put a citation's chip on a sentence: the activity id and name, then the
    identifiers quoted in the citation text; common single words are left out."""
    names = [activity_id or "", activity_name or "", *_WORD.findall(quote or "")]
    return list(dict.fromkeys(n.strip() for n in names if _identifier_like(n)))


def activity_ref(ref_id: str, process_id: str, ir_id: str, activity_id: str, activity_name: str,
                 quote: str, *, verified: bool | None, reason: str | None,
                 phase: UiPhase) -> CiteRef:
    return CiteRef(
        id=ref_id, phase=phase, kind="activity",
        label=cut(mask_sensitive(activity_name or activity_id or "activity"), LABEL_MAX),
        preview=cut(mask_sensitive(quote or ""), PREVIEW_MAX),
        memory_uid=activity_uid(ir_id, activity_id) if activity_id else None,
        verified=verified, reason=reason,
        target=CiteTarget(kind="activity", process_id=process_id, activity_id=activity_id or None))


def activity_names(process: Any) -> dict[str, str]:
    """Activity id to name from ``Process.ir``: build it once per view, pass it to each claim."""
    return {a.get("id"): a.get("name") or "" for a in (process.ir or {}).get("activities") or []}


def _cite_claim(key: str, text: str | None, citations: list[Any], process: Any,
                names: dict[str, str] | None) -> CitedText:
    """``cited.text`` is the masked claim text and every span indexes into it."""
    names = activity_names(process) if names is None else names
    hinted = []
    for i, c in enumerate(citations):
        c = c if isinstance(c, dict) else c.model_dump()
        aid = c.get("activity_id") or ""
        name = names.get(aid, "")
        ref = activity_ref(f"c{i + 1}", process.id, process.ir_id, aid, name, c.get("text") or "",
                           verified=c.get("verified"), reason=c.get("reason"), phase="validate")
        hinted.append(HintedRef(ref=ref, hints=hints_for(aid, name, c.get("text"))))
    text = mask_sensitive(text or "")
    if not text.strip():
        return tail(key, [h.ref for h in hinted])
    return anchor(text, hinted)


def cite_rule(rule: Any, process: Any, citations: list[dict[str, Any]] | None = None, *,
              names: dict[str, str] | None = None) -> CitedText:
    """``citations`` overrides ``rule.citations`` (the view's, carrying ``verified``/``reason``);
    ``names`` is ``activity_names(process)``, built once per view."""
    return _cite_claim(rule.rule_key, rule.text,
                       rule.citations or [] if citations is None else citations, process, names)


def cite_finding(finding: Any, process: Any, *, names: dict[str, str] | None = None) -> CitedText:
    return _cite_claim(finding.finding_key, finding.text, finding.citations or [], process, names)


def cite_domain(domain: Any) -> CitedText:
    """One rationale sentence per evidence kind (queues and topics share one), in order of first
    appearance; one ref per evidence item, hinted by its masked text: it anchors on the first
    sentence that names that text (its own kind's, unless an earlier sentence also holds it),
    else on the last sentence."""
    items = [(str(e.get("kind") or ""), mask_sensitive(str(e.get("text") or "")))
             for e in domain.evidence or []]
    by_kind: dict[str, list[str]] = {}
    for kind, text in items:
        by_kind.setdefault(_SAME_SENTENCE.get(kind, kind), []).append(text)
    sentences = []
    for kind, texts in by_kind.items():
        template = _DOMAIN_SENTENCE.get(kind) or f"{(kind or 'evidence').capitalize()}: {{}}."
        sentences.append(template.format(", ".join(texts)))
    rationale = " ".join(sentences)
    hinted = [HintedRef(
        ref=CiteRef(id=f"c{i + 1}", phase="discover", kind="evidence",
                    label=cut(text or kind or "evidence", LABEL_MAX), preview=cut(text, PREVIEW_MAX),
                    target=CiteTarget(kind="step", step="discover")),
        hints=[text]) for i, (kind, text) in enumerate(items)]
    return anchor(rationale, hinted) if hinted else CitedText.plain(rationale)
