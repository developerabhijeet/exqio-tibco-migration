"""JSON output contracts for the agents and a tolerant parser for model replies."""
from __future__ import annotations

import json
from typing import Any

from engine.analysis.models import Citation

_CITATION_KINDS = ("sql", "expression", "condition", "config", "schema")

_CITATION = {
    "type": "object",
    "properties": {
        "activity_id": {"type": "string"},
        "kind": {"enum": ["sql", "expression", "condition", "config", "schema"]},
        "text": {"type": "string", "description": "Text copied verbatim from the source"},
    },
    "required": ["activity_id", "kind", "text"],
}

ANALYST_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": _CITATION},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "memory_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "citations", "confidence"],
            },
        },
        "technical": {
            "type": "array",
            "description": "Activities or transitions that carry no business rule",
            "items": {
                "type": "object",
                "properties": {"ref": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["ref", "reason"],
            },
        },
    },
    "required": ["summary", "rules", "technical"],
}

RISK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "impacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "business_impact": {"type": "string"},
                },
                "required": ["finding_id", "business_impact"],
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"enum": ["high", "medium", "low"]},
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": _CITATION},
                    "business_impact": {"type": "string"},
                },
                "required": ["severity", "text", "citations", "business_impact"],
            },
        },
    },
    "required": ["impacts", "findings"],
}

CHALLENGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "verdict": {"enum": ["agree", "disagree"]},
                    "reason": {"type": "string"},
                },
                "required": ["rule_id", "verdict", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

DOMAIN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "domains": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                "required": ["id", "name"],
            },
        }
    },
    "required": ["domains"],
}


class JsonOutputError(ValueError):
    """The model reply did not contain a JSON object."""


def json_instruction(schema: dict) -> str:
    """System-prompt suffix that asks for one JSON object. Also satisfies providers whose JSON
    mode requires the word JSON in the messages."""
    return (
        "\n\nReply with one JSON object and nothing else. No prose, no code fences. "
        "The object must match this JSON schema:\n" + json.dumps(schema)
    )


def parse_json_block(text: str) -> dict:
    """Parse the first JSON object in ``text``; tolerant of code fences and surrounding prose."""
    if not text or not text.strip():
        raise JsonOutputError("empty reply")
    decoder = json.JSONDecoder()
    s = text.strip()
    pos = 0
    while True:
        start = s.find("{", pos)
        if start < 0:
            break
        try:
            obj, _ = decoder.raw_decode(s, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(obj, dict):
            return obj
        pos = start + 1
    raise JsonOutputError("no JSON object found in the reply")


def coerce_citations(raw: Any) -> list[Citation]:
    """Keep the well-formed citations from a model reply and drop the rest. A citation needs an
    activity id, a known kind and non-empty text."""
    out: list[Citation] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        activity_id, kind, text = item.get("activity_id"), item.get("kind"), item.get("text")
        if (
            isinstance(activity_id, str) and activity_id.strip()
            and kind in _CITATION_KINDS
            and isinstance(text, str) and text.strip()
        ):
            out.append(Citation(activity_id=activity_id.strip(), kind=kind, text=text))
    return out


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
