"""Safe XML parsing for tool-produced reports: no entities, no network, bounded size."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

MAX_REPORT_BYTES = 64 * 1024 * 1024
_OPTS = {"resolve_entities": False, "load_dtd": False, "no_network": True, "huge_tree": False}


def _reject_entity_declarations(p: Path) -> None:
    """Read as far as the root element (prolog and DOCTYPE, in any encoding) and refuse entity declarations."""
    with p.open("rb") as fh:
        for _event, el in etree.iterparse(fh, events=("start",), **_OPTS):
            dtd = el.getroottree().docinfo.internalDTD
            if dtd is not None and any(True for _ in dtd.iterentities()):
                raise etree.XMLSyntaxError("entity declarations are not allowed in reports", 0, 0, 0)
            return


def parse_xml(path: str | Path):
    p = Path(path)
    if p.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"{p.name} is larger than {MAX_REPORT_BYTES} bytes")
    _reject_entity_declarations(p)
    return etree.parse(str(p), etree.XMLParser(**_OPTS)).getroot()
