#!/usr/bin/env python3
"""Build-time egress audit: which hosts could our own code or shipped web bundle call?

GM Financial needs evidence that, once installed, nothing in the platform's code or in the bundle
its browsers load talks to a host outside its tenant. Runtime egress is enforced by NetworkPolicy
and the firewall; this audit is the static counterpart. It lists every host named in the scanned
files and fails (exit 1) for any that the allowlist does not cover.

    egress_audit.py <dir-or-image>... --allowlist deploy/egress-allowlist.txt [--allow-optional claude]

A directory is scanned as is. Anything else is a Docker image: it is created (never started) and
only the paths in --image-path (default /app, where the platform image keeps our code) are copied out into
a temporary directory that is removed afterwards. Third-party runtimes (/opt/venv, the .NET SDK,
/usr) are out of scope. For the web image pass the standalone app's own tree (server.js,
.next/server, .next/static, public): the bundled third-party JS in it ships to the browser and so
is in scope, while the server's node_modules (Next's build tooling) is a third-party runtime. Python files under a "tests" directory are
skipped: they are fixtures, and .dockerignore keeps them out of the image.

What counts as a host:
  * .py files: URLs and bare FQDNs inside string literals only (tokenize), never docstrings or
    comments, so documentation links do not count.
  * Templates and config (.j2, .tf, .csproj, .cs, .sql, Dockerfile, .env*, ...) are scanned like
    other text. Protocol-relative URLs ("//cdn.host/x.js") count as hosts too.
  * .js/.json/.html/.css/.md/... files: URLs anywhere in the text, and quoted bare FQDNs.
Ignored: RFC 2606/6761 example names, localhost, single-label and .local names, private IPs, XML namespace and schema
URIs (w3.org, schemas.*, the TIBCO namespaces the parser matches on, xmlns attribute values) and
URLs built from a template (`https://${host}`), which name no fixed host.

Next.js telemetry lives in node_modules/next/dist/telemetry (outside the web scope above) and is
driven by the next CLI (build, dev), not by `node server.js`; the web image also sets
NEXT_TELEMETRY_DISABLED=1 in both stages (deploy/docker/web.Dockerfile).

Allowlist: one host pattern per line (`*.vault.azure.net` matches subdomains only, anything else is
an exact host). Every entry needs a comment saying why it is allowed, either directly above it (a
blank line ends the block) or inline. `# optional: <name>` opens a section that only applies with
--allow-optional <name>; `# end optional` closes it.
"""
from __future__ import annotations

import argparse
import ast
import io
import ipaddress
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from dataclasses import dataclass
from pathlib import Path

TEXT_EXTS = {
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".json", ".html", ".htm", ".css", ".md",
    ".yaml", ".yml", ".sh", ".toml", ".txt",
    # Shipped templates that generate customer code and infrastructure, and config formats.
    ".tf", ".tfvars", ".hcl", ".tpl", ".j2", ".mako", ".props", ".csproj", ".xml", ".cs", ".sql",
    ".ini", ".cfg", ".conf", ".env", ".ps1", ".sln",
}
TEXT_NAMES = ("dockerfile", "dockerignore", "gitignore", "editorconfig", "makefile")
PY_EXT = ".py"

URL_RE = re.compile(
    r"(?P<scheme>https?|wss?|ftp)://(?:[^/\s\"'`<>@\\]*@)?(?P<host>\[[0-9a-fA-F:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]*)"
    r"(?P<next>[^A-Za-z0-9.-]|$)",
    re.IGNORECASE,
)
# Protocol-relative URL ("//cdn.vendor.io/x.js"): a string or attribute value that starts with //.
# The lookbehind rules out "https://..." (preceded by ":") and mid-word slashes; comments are gone
# before this runs (JS) or never reach it (Python tokenize).
PROTO_REL_RE = re.compile(
    r"""(?:(?<=["'`(=])|(?<=[=:]\s))//(?P<host>(?:[a-z0-9-]+\.)+[a-z]{2,})(?=[/:"'`\s?#)>]|$)""", re.IGNORECASE)
# A whole quoted string that is just a host (optionally with port or path), e.g. "vendor.io:443".
QUOTED_FQDN_RE = re.compile(r"""["'`](?P<host>(?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?(?:/[^"'`\s]*)?["'`]""", re.IGNORECASE)
BARE_FQDN_RE = re.compile(r"(?P<host>(?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?(?:/\S*)?", re.IGNORECASE)
# Bare names are only taken for common TLDs: "config.json", "os.path" or "div.info" (a CSS
# selector) are not hosts. URLs with any TLD are always checked.
TLDS = {"com", "net", "org", "io", "ai", "dev", "app", "cloud", "co", "us", "gov", "edu"}
XMLNS_BEFORE_RE = re.compile(r"""xmlns(?::[\w.-]+)?\s*=\s*["']?\s*$""", re.IGNORECASE)
NAMESPACE_PREFIXES = (
    "http://www.w3.org/",
    "http://schemas.",
    "http://www.tibco.com/",
    # The URIs engine/tibco_parser matches elements on (xmlutil.NS, bw6, resources); namespaces,
    # not hosts: BPEL/SCA (OASIS), the Eclipse notation model, XMI (OMG), the TRX schema.
    "http://xmlns.tibco.com/",
    "http://tns.tibco.com/",
    "http://xsd.tns.tibco.com/",
    "http://ns.tibco.com/",
    "http://docs.oasis-open.org/",
    "http://www.eclipse.org/",
    "http://www.omg.org/",
    "http://microsoft.com/schemas/",
)
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = ("example", "test", "invalid", "localhost")
SKIP_DIRS = {"__pycache__"}
TEST_DIRS = {"tests"}  # Python fixtures only; .dockerignore keeps them out of the images
TEMPLATE_CHARS = "${%<"


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    host: str

    def format(self, root: Path | None = None) -> str:
        p = self.path
        if root is not None:
            try:
                p = p.relative_to(root)
            except ValueError:
                pass
        return f"{p}:{self.line} {self.host}"


class Allowlist:
    def __init__(self, patterns: list[str]):
        self.patterns = patterns

    def allows(self, host: str) -> bool:
        for pat in self.patterns:
            if pat.startswith("*."):
                if host.endswith(pat[1:]) and len(host) > len(pat) - 1:
                    return True
            elif host == pat:
                return True
        return False


def load_allowlist(path: Path, optional: tuple[str, ...] | list[str] = (),
                   require_comments: bool = False) -> Allowlist:
    patterns: list[str] = []
    section: str | None = None
    commented = False
    for n, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            commented = False
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*optional:\s*(\S+)", line)
            if m:
                section = m.group(1)
            elif re.match(r"#\s*end optional\b", line):
                section = None
            commented = True
            continue
        entry, _, inline = line.partition("#")
        entry = entry.strip().lower()
        if require_comments and not (commented or inline.strip()):
            raise ValueError(f"{path}:{n} {entry}: allowlist entry has no comment saying why")
        if section is None or section in optional:
            patterns.append(entry)
    return Allowlist(patterns)


def _ignored(host: str, scheme: str, url_start: str) -> bool:
    if "." not in host.strip("[]") and not host.startswith("["):
        return True  # single-label name (http://worker:9100): cluster-internal DNS, never public
    if host.endswith(".local"):
        return True  # RFC 6762 multicast-DNS suffix: not a delegated name, cannot leave the LAN
    if host.startswith("["):
        host = host[1:-1]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
    if host in EXAMPLE_DOMAINS or any(host.endswith("." + d) for d in EXAMPLE_DOMAINS):
        return True
    return host.rsplit(".", 1)[-1] in RESERVED_TLDS


def _candidates(text: str, quoted_fqdn: bool):
    """Yield (offset, host) for every non-ignored host named in text."""
    for m in URL_RE.finditer(text):
        host = m.group("host").lower().rstrip(".")
        if m.group("host").endswith(".") and m.group("next") in TEMPLATE_CHARS:
            continue  # https://foo.{region}.x: the host itself is incomplete, so no fixed host
        before = text[max(0, m.start() - 40):m.start()]
        if XMLNS_BEFORE_RE.search(before):
            continue
        url = text[m.start():m.start() + 80].lower()
        if url.startswith(NAMESPACE_PREFIXES) or url.startswith("https://www.w3.org/"):
            continue
        if _ignored(host, m.group("scheme"), before):
            continue
        yield m.start(), host
    for m in PROTO_REL_RE.finditer(text):
        host = m.group("host").lower()
        if not _ignored(host, "", ""):
            yield m.start(), host
    if quoted_fqdn:
        for m in QUOTED_FQDN_RE.finditer(text):
            host = m.group("host")
            if host != host.lower():
                continue  # "Microsoft.AspNetCore.App": a dotted identifier, not a host
            if host.rsplit(".", 1)[-1] in TLDS and not _ignored(host, "", ""):
                yield m.start(), host


def _docstring_positions(src: str) -> set[tuple[int, int]]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.add((first.value.lineno, first.value.col_offset))
    return out


def scan_python(path: Path, src: str) -> list[Violation]:
    docs = _docstring_positions(src)
    found: list[tuple[int, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return scan_text(path, src)  # unparsable: fall back to scanning the raw text
    fstring_mid = getattr(tokenize, "FSTRING_MIDDLE", None)
    for tok in tokens:
        if tok.type == tokenize.STRING:
            if tok.start in docs:
                continue
            raw = tok.string
            try:
                value = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                value = raw
            if isinstance(value, bytes):
                continue
            # URLs from the raw text for exact lines; a whole-literal bare FQDN from the value.
            for off, host in _candidates(raw, quoted_fqdn=False):
                found.append((tok.start[0] + raw.count("\n", 0, off), host))
            if isinstance(value, str) and "\n" not in value:
                m = BARE_FQDN_RE.fullmatch(value.strip())
                if m and m.group("host") == m.group("host").lower():
                    host = m.group("host")
                    if host.rsplit(".", 1)[-1] in TLDS and not _ignored(host, "", ""):
                        found.append((tok.start[0], host))
        elif fstring_mid is not None and tok.type == fstring_mid:
            for off, host in _candidates(tok.string, quoted_fqdn=False):
                found.append((tok.start[0] + tok.string.count("\n", 0, off), host))
    return [Violation(path, line, host) for line, host in sorted(set(found))]


JS_EXTS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".css"}
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")


def strip_js_comments(text: str) -> str:
    """text with // and /* */ comments blanked out (newlines kept, so line numbers hold).

    Documentation links in comments (MDN, license headers, sourcemap notes) are never fetched.
    A small scanner that knows strings, template literals and regex literals, so a "//" inside
    "https://host" is left alone. When in doubt it keeps text: a missed comment only costs a false
    finding, while a string mistaken for a comment would hide a host.
    """
    out: list[str] = []
    i, n = 0, len(text)
    templates: list[int] = []  # brace depth at each open `${` inside a template literal
    depth = 0
    last = ""  # last significant character, to tell a regex literal from a division
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            i = j
            continue
        if c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("\n" * text.count("\n", i, j))
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i, last = j + 1, c
            continue
        if c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                if text[j] == "\\":
                    j += 2
                elif text[j] == "$" and text[j + 1:j + 2] == "{":
                    break
                else:
                    j += 1
            if text[j:j + 2] == "${":
                templates.append(depth)
                depth += 1
                out.append(text[i:j + 2])
                i, last = j + 2, "{"
            else:
                out.append(text[i:j + 1])
                i, last = j + 1, "`"
            continue
        if c == "/" and (last == "" or last in _REGEX_PRECEDERS):
            j = i + 1
            in_class = False
            while j < n and text[j] != "\n" and (in_class or text[j] != "/"):
                if text[j] == "\\":
                    j += 1
                elif text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                j += 1
            out.append(text[i:j + 1])
            i, last = j + 1, "/"
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if templates and depth == templates[-1]:
                templates.pop()  # back inside the template literal after `${ ... }`
                j = i + 1
                while j < n and text[j] != "`":
                    if text[j] == "\\":
                        j += 2
                    elif text[j] == "$" and text[j + 1:j + 2] == "{":
                        break
                    else:
                        j += 1
                if text[j:j + 2] == "${":
                    templates.append(depth)
                    depth += 1
                    out.append(text[i:j + 2])
                    i, last = j + 2, "{"
                else:
                    out.append(text[i:j + 1])
                    i, last = j + 1, "`"
                continue
        out.append(c)
        if not c.isspace():
            last = c
        i += 1
    return "".join(out)


def scan_text(path: Path, text: str) -> list[Violation]:
    if path.suffix.lower() in JS_EXTS:
        text = strip_js_comments(text)
    out: set[Violation] = set()
    for n, line in enumerate(text.splitlines(), 1):
        if "://" not in line and "." not in line:
            continue
        for _, host in _candidates(line, quoted_fqdn=True):
            out.add(Violation(path, n, host))
    return sorted(out, key=lambda v: (v.line, v.host))


def scan_file(path: Path) -> list[Violation]:
    ext = path.suffix.lower()
    name = path.name.lower()
    known = (ext in TEXT_EXTS or name.startswith(TEXT_NAMES) or name.startswith(".env")
             or name.endswith(".dockerfile"))
    if ext != PY_EXT and not known:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_python(path, text) if ext == PY_EXT else scan_text(path, text)


def audit_dirs(dirs: list[Path], allowlist: Allowlist) -> list[Violation]:
    violations: list[Violation] = []
    for d in dirs:
        d = Path(d)
        files = [d] if d.is_file() else sorted(d.rglob("*"))
        for f in files:
            rel = f.relative_to(d) if d.is_dir() else Path(f.name)
            dirs_above = set(rel.parts[:-1])
            if f.is_symlink() or not f.is_file() or dirs_above & SKIP_DIRS:
                continue
            if f.suffix == PY_EXT and dirs_above & TEST_DIRS:
                continue
            violations += [v for v in scan_file(f) if not allowlist.allows(v.host)]
    return violations


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def extract_image(image: str, paths: list[str], dest: Path) -> list[Path]:
    """Copy paths out of a created (never started) container of image into dest."""
    cid = _run("docker", "create", image)
    try:
        roots = []
        for p in paths:
            target = dest / p.strip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["docker", "cp", f"{cid}:{p}", str(target)],
                           check=True, capture_output=True)
            roots.append(target)
        return roots
    finally:
        subprocess.run(["docker", "rm", "-f", cid], check=False, capture_output=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("targets", nargs="+", help="directory, or Docker image reference")
    ap.add_argument("--allowlist", required=True, type=Path)
    ap.add_argument("--allow-optional", action="append", default=[], metavar="NAME")
    ap.add_argument("--image-path", action="append", default=None, metavar="PATH",
                    help="path inside an image to audit (repeatable; default /app)")
    args = ap.parse_args(argv)
    allow = load_allowlist(args.allowlist, args.allow_optional)
    failed = False
    for target in args.targets:
        tmp: Path | None = None
        try:
            if Path(target).exists():
                roots, base = [Path(target)], Path(target)
            else:
                tmp = Path(tempfile.mkdtemp(prefix="egress-audit-"))
                roots = extract_image(target, args.image_path or ["/app"], tmp)
                base = tmp
            found = audit_dirs(roots, allow)
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
        for v in found:
            print(f"{target}: {v.format(base)}")
        n_hosts = len({v.host for v in found})
        print(f"egress-audit: {target}: {'FAIL' if found else 'ok'} "
              f"({len(found)} violation(s), {n_hosts} host(s))")
        failed |= bool(found)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
