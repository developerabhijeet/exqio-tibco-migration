"""Small XML helpers shared by every TIBCO parser.

`read_xml` is the single entry point for loading project files: it normalises
CRLF to LF at the byte level (so `sourceline` values match what an editor
shows on any platform) and refuses to resolve entities or touch the network.
"""
from pathlib import Path

from lxml import etree

NS: dict[str, str] = {
    "bpws": "http://docs.oasis-open.org/wsbpel/2.0/process/executable",
    "tibex": "http://www.tibco.com/bpel/2007/extensions",
    "pd": "http://xmlns.tibco.com/bw/process/2003",
    "xsl": "http://www.w3.org/1999/XSL/Transform",
    "xsd": "http://www.w3.org/2001/XMLSchema",
    "wsdl": "http://schemas.xmlsoap.org/wsdl/",
    "jdbc": "http://xsd.tns.tibco.com/amf/models/sharedresource/jdbc",
    "http": "http://xsd.tns.tibco.com/bw/models/sharedresource/httpclient",
    "jms": "http://xsd.tns.tibco.com/bw/models/sharedresource/jms",
    "tibprop": "http://ns.tibco.com/bw/property",
}


def normalize_bytes(data: bytes) -> bytes:
    """Replace CRLF with LF (keeps line numbers aligned with the LF form)."""
    return data.replace(b"\r\n", b"\n")


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=True,
        remove_comments=False,
    )


def read_xml(path: Path) -> etree._Element:
    """Parse `path` and return the root element; `sourceline` is preserved."""
    data = normalize_bytes(Path(path).read_bytes())
    return etree.fromstring(data, _parser())


def local(tag: object) -> str:
    """Local name of a Clark-notation tag; '' for comments and PIs."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag
