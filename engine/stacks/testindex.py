"""The generated-test index for stacks whose test names carry no ids (Java, Go, Python).

Written beside the generated project at ``.exiqo/test-index.json``; run results are joined to it by
test name, giving the same neutral ``trx.TestCase`` the .NET path produces.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from engine.dotnet_gen.generator import GenFile
from engine.dotnet_gen.trx import TestCase

log = logging.getLogger(__name__)
INDEX_PATH = ".exiqo/test-index.json"


class IndexEntry(BaseModel):
    name: str
    kind: Literal["rule", "branch", "scenario", "record", "other"] = "other"
    rule_id: str | None = None
    branch_id: str | None = None
    scenario_id: str | None = None
    display_name: str = ""
    process_id: str | None = None
    path: str = ""
    has_body: bool = True


def write_index(entries: list[IndexEntry]) -> GenFile:
    body = json.dumps([e.model_dump() for e in entries], indent=2, sort_keys=True) + "\n"
    return GenFile(path=INDEX_PATH, content=body)


def read_index_file(path: Path) -> list[IndexEntry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [IndexEntry.model_validate(d) for d in data]


def read_index(root: Path) -> list[IndexEntry]:
    p = Path(root) / INDEX_PATH
    return read_index_file(p) if p.is_file() else []


_PARAMS = re.compile(r"\[[^\]]*\]$")


def _lookup(index: list[IndexEntry]):
    exact: dict[str, IndexEntry] = {}
    for e in index:
        if e.name in exact:
            log.warning("duplicate test index name %s; first entry wins", e.name)
            continue
        exact[e.name] = e

    def find(name: str) -> IndexEntry | None:
        name = _PARAMS.sub("", name)  # pytest parametrize / JUnit display suffix
        if name in exact:
            return exact[name]
        # the tool may add or drop a package prefix; accept it only on a dotted boundary and
        # only when the shared tail still names the class as well as the method
        hits = [e for e in exact.values()
                if (name.endswith("." + e.name) and e.name.count(".") >= 1)
                or (e.name.endswith("." + name) and name.count(".") >= 1)]
        return hits[0] if len(hits) == 1 else None

    return find


def join(cases: list[tuple[str, str, float, str]], index: list[IndexEntry]) -> list[TestCase]:
    find = _lookup(index)
    out: list[TestCase] = []
    for name, outcome, duration_ms, message in cases:
        e = find(name)
        out.append(TestCase(
            name=e.name if e else name,  # the index name, so results merge with the inventory
            display_name=(e.display_name if e and e.display_name else name),
            outcome=outcome,  # type: ignore[arg-type]
            message=message,
            rule_id=e.rule_id if e else None,
            branch_id=e.branch_id if e else None,
            scenario_id=e.scenario_id if e else None,
            duration_ms=duration_ms,
        ))
    return out
