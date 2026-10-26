"""Safe XML parsing for tool-produced reports: no entities, no network, bounded size."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

MAX_REPORT_BYTES = 64 * 1024 * 1024


def parse_xml(path: str | Path):
    p = Path(path)
    if p.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"{p.name} is larger than {MAX_REPORT_BYTES} bytes")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    return etree.parse(str(p), parser).getroot()
