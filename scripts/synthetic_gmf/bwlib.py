"""Small XML writer that formats like BusinessWorks Studio 6.x (sorted attributes,
xmlns last, 80-column attribute wrapping, 4-space indent)."""
from __future__ import annotations

import base64
import hashlib
import re
import textwrap
import uuid

XSL = "http://www.w3.org/1999/XSL/Transform"
XSLT_LANG = "urn:oasis:names:tc:wsbpel:2.0:sublang:xslt1.0"
XPATH_LANG = "urn:oasis:names:tc:wsbpel:2.0:sublang:xpath2.0"
TIBEX = "http://www.tibco.com/bpel/2007/extensions"


def xpdl(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "gmf-synthetic:" + seed))


def xmi(seed: str) -> str:
    raw = hashlib.md5(("xmi:" + seed).encode()).digest()
    return "_" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def obfuscated(seed: str) -> str:
    raw = hashlib.sha256(("pw:" + seed).encode()).digest()[:24]
    return "#!" + base64.b64encode(raw).decode()


def esc_attr(v: str) -> str:
    return (v.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
            .replace("\n", "&#xa;"))


def esc_text(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Raw:
    def __init__(self, text: str):
        self.text = textwrap.dedent(text).strip("\n")

    def lines(self, indent: int) -> list[str]:
        return [(" " * indent + ln) if ln.strip() else "" for ln in self.text.splitlines()]


class N:
    def __init__(self, tag, attrs=None, children=None, text=None, cdata=None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children = [c for c in (children or []) if c is not None]
        self.text = text
        self.cdata = cdata

    def _ordered(self):
        plain = sorted((k, v) for k, v in self.attrs.items() if not k.startswith("xmlns"))
        decl = sorted((k, v) for k, v in self.attrs.items() if k.startswith("xmlns"))
        return plain + decl

    def lines(self, indent: int = 0) -> list[str]:
        out: list[str] = []
        cur = " " * indent + "<" + self.tag
        first = True
        for k, v in self._ordered():
            part = f'{k}="{esc_attr(v)}"'
            if not first and len(cur) + 1 + len(part) > 80:
                out.append(cur)
                cur = " " * (indent + 4) + part
            else:
                cur += " " + part
            first = False
        if not self.children and self.text is None and self.cdata is None:
            out.append(cur + "/>")
            return out
        if self.cdata is not None:
            out.append(cur + f"><![CDATA[{self.cdata}]]></{self.tag}>")
            return out
        if self.text is not None and not self.children:
            out.append(cur + ">" + esc_text(self.text) + f"</{self.tag}>")
            return out
        out.append(cur + ">")
        for c in self.children:
            out.extend(c.lines(indent + 4))
        out.append(" " * indent + f"</{self.tag}>")
        return out


def render(node: N, decl: str = '<?xml version="1.0" encoding="UTF-8"?>') -> str:
    return decl + "\n" + "\n".join(node.lines(0)) + "\n"


PREFIX_RE = re.compile(r"(?<![\w$.-])(tns\d*|xsd|xs):[A-Za-z_]")


def xslt(prefixes: dict[str, str], params: list[str], template: str, body: str) -> str:
    """Studio-style mapping stylesheet. Only prefixes used in the body are declared."""
    used = {m.group(1) for m in PREFIX_RE.finditer(body)}
    decls = " ".join(f'xmlns:{p}="{prefixes[p]}"' for p in sorted(used, key=_pkey))
    head = f'<xsl:stylesheet xmlns:xsl="{XSL}"' + (f" {decls}" if decls else "") + ' version="2.0">'
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', head]
    lines += [f'    <xsl:param name="{p}"/>' for p in params]
    lines.append(f'    <xsl:template name="{template}" match="/">')
    lines += ["        " + ln for ln in textwrap.dedent(body).strip("\n").splitlines()]
    lines.append("    </xsl:template>")
    lines.append("</xsl:stylesheet>")
    return "\n".join(lines)


def _pkey(p: str):
    m = re.match(r"tns(\d*)$", p)
    if m:
        return (0, int(m.group(1) or -1))
    return (1, p)
