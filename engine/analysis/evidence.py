"""Evidence index: where a citation's text may legitimately come from.

``EvidenceIndex(process)`` collects, per activity, the verbatim IR text a rule
may quote, split by citation kind:

* ``sql``        the activity's SQL (``config["sql"]`` and ``settings.sqlStatement``/``statement``);
* ``expression`` its input-binding expressions (the mappings);
* ``condition``  the expressions of its outgoing transitions;
* ``config``     every string value in its config, nested settings included;
* ``schema``     its output schema name.

Groups and fault handlers are addressable too (a transition can leave a group;
a ``catch`` is cited by its fault name), so findings about them can be verified.

Matching is a substring test after removing all whitespace: a model that
re-flows ``numofpulls + 1`` as ``numofpulls+1`` still matches, a paraphrase does not.
"""
from __future__ import annotations

import re
from typing import Any

from engine.ir.models import Process

_WS = re.compile(r"\s+")
_SQL_KEYS = {"sql", "sqlstatement", "statement"}


def normalize(text: str) -> str:
    """Whitespace-insensitive form used for every citation comparison."""
    return _WS.sub("", text)


def iter_config_strings(value: Any, key: str = ""):
    """Yield ``(key, string)`` for every string leaf of a config value (dict keys give ``key``)."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from iter_config_strings(v, str(k))
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from iter_config_strings(v, key)
    elif value is not None:
        yield key, value if isinstance(value, str) else str(value)


class EvidenceIndex:
    def __init__(self, process: Process):
        self._raw: dict[str, dict[str, list[str]]] = {}
        for act in process.activities:
            sql: list[str] = []
            config: list[str] = []
            for key, text in iter_config_strings(act.config):
                config.append(text)
                if key.lower() in _SQL_KEYS:
                    sql.append(text)
            self._raw[act.id] = {
                "sql": sql,
                "expression": [b.expression for b in act.input_bindings],
                "condition": [],
                "config": config,
                "schema": [act.output_schema] if act.output_schema else [],
            }
        for grp in process.groups:
            self._raw.setdefault(grp.id, self._empty())["config"].extend(
                text for _, text in iter_config_strings(grp.config)
            )
        for fh in process.fault_handlers:
            entry = self._raw.setdefault(fh.id, self._empty())
            entry["config"].extend(t for t in (fh.id, fh.fault_name) if t)
        for tr in process.transitions:
            if tr.expression:
                self._raw.setdefault(tr.from_id, self._empty())["condition"].append(tr.expression)
        self._norm = {
            aid: {kind: [normalize(t) for t in texts] for kind, texts in kinds.items()}
            for aid, kinds in self._raw.items()
        }

    @staticmethod
    def _empty() -> dict[str, list[str]]:
        return {"sql": [], "expression": [], "condition": [], "config": [], "schema": []}

    def known(self, activity_id: str) -> bool:
        return activity_id in self._raw

    def texts(self, activity_id: str, kind: str) -> list[str]:
        """The original (un-normalised) candidate texts for one activity and kind."""
        return list(self._raw.get(activity_id, {}).get(kind, []))

    def has(self, activity_id: str, kind: str, text: str) -> bool:
        needle = normalize(text)
        if not needle:
            return False
        return any(needle in cand for cand in self._norm.get(activity_id, {}).get(kind, []))
