"""What a revision changed: versions opened and closed at it, classified by uid."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class VersionRef(BaseModel):
    uid: str
    kind: str  # node label or relationship type
    name: str
    content_hash: str
    props: dict[str, Any]


class ChangedRef(VersionRef):
    changed_keys: list[str]


class DiffSet(BaseModel):
    added: list[VersionRef]
    changed: list[ChangedRef]
    removed: list[VersionRef]


def changed_keys(old: VersionRef, new: VersionRef) -> list[str]:
    """The property keys (and ``name``) whose values differ between two versions."""
    keys = {k for k in old.props.keys() | new.props.keys() if old.props.get(k) != new.props.get(k)}
    if old.name != new.name:
        keys.add("name")
    return sorted(keys)


def classify(opened: list[VersionRef], closed: list[VersionRef]) -> DiffSet:
    """By uid: opened and closed with the same hash is a re-attachment (not reported); opened
    only is added; closed only is removed; opened and closed with different hashes is changed,
    carrying the new version and the property keys that differ."""
    closed_by_uid = {c.uid: c for c in closed}
    opened_uids = {o.uid for o in opened}
    added: list[VersionRef] = []
    changed: list[ChangedRef] = []
    for o in opened:
        c = closed_by_uid.get(o.uid)
        if c is None:
            added.append(o)
        elif c.content_hash != o.content_hash:
            changed.append(ChangedRef(**o.model_dump(), changed_keys=changed_keys(c, o)))
    removed = [c for c in closed if c.uid not in opened_uids]
    return DiffSet(added=added, changed=changed, removed=removed)
