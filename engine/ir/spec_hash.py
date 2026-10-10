"""Spec hash: SHA-256 over canonical JSON of IR, approved rules and finding decisions.

Gates G1 and G2 bind to this value (design spec section 3.1).
"""
import hashlib
import json
from typing import Any, Dict, List


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _stable(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order-independent when entries carry an ``id``; otherwise sorted by canonical JSON."""
    return sorted(items, key=lambda x: (str(x.get("id", "")), canonical_json(x)))


def spec_hash(process_ir: Dict[str, Any], rules: List[Dict[str, Any]], decisions: List[Dict[str, Any]]) -> str:
    """Return the SHA-256 hex digest binding an IR, its approved rules and finding decisions.

    Dict key order is irrelevant. Rules and decisions are sorted (by ``id``,
    then by canonical JSON) before hashing, so list order does not matter and
    callers need not sort. The IR's own lists keep their order (activity order
    is meaningful).
    """
    payload = canonical_json({"ir": process_ir, "rules": _stable(rules), "decisions": _stable(decisions)})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
