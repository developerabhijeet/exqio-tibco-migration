"""Spec hash: SHA-256 over canonical JSON of IR, approved rules and finding decisions.

Gates G1 and G2 bind to this value (design spec section 3.1).
"""
import hashlib
import json
from typing import Any, Dict, List


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def spec_hash(process_ir: Dict[str, Any], rules: List[Dict[str, Any]], decisions: List[Dict[str, Any]]) -> str:
    """Return the SHA-256 hex digest binding an IR, its approved rules and finding decisions.

    Dict key order is irrelevant; list order is significant, so callers should
    pass rules and decisions in a stable order (for example sorted by id).
    """
    payload = canonical_json({"ir": process_ir, "rules": rules, "decisions": decisions})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
