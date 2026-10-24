"""Egress audit: which hosts our own code and shipped web bundle could call.

The audit is the build-time evidence behind GM Financial's "nothing leaves the tenant" claim; the
last test runs it over the repo's engine/ and api/ source so CI catches a new hard-coded host
without Docker.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import egress_audit as ea

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = REPO / "deploy" / "egress-allowlist.txt"

BASE_ALLOW = """\
# GMF tenant hosts
login.microsoftonline.com
*.vault.azure.net
# optional: claude
api.anthropic.com
"""


def audit(tmp_path: Path, files: dict[str, str], optional: tuple[str, ...] = ()):
    for name, text in files.items():
        p = tmp_path / "src" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    allow = tmp_path / "allow.txt"
    allow.write_text(BASE_ALLOW)
    return ea.audit_dirs([tmp_path / "src"], ea.load_allowlist(allow, optional))


def test_allowlisted_host_passes(tmp_path):
    assert audit(tmp_path, {"a.js": 'fetch("https://login.microsoftonline.com/x")'}) == []


def test_wildcard_matches_subdomain_only(tmp_path):
    ok = audit(tmp_path, {"a.js": 'u = "https://gmf.vault.azure.net/secrets"'})
    assert ok == []
    bare = audit(tmp_path, {"b.js": 'u = "https://vault.azure.net/"'})
    assert [v.host for v in bare] == ["vault.azure.net"]


def test_unlisted_host_in_js_reports_file_and_line(tmp_path):
    out = audit(tmp_path, {"a.js": 'const a = 1;\nfetch("https://huggingface.co/x")\n'})
    assert [(v.path.name, v.line, v.host) for v in out] == [("a.js", 2, "huggingface.co")]
    assert "a.js:2 huggingface.co" in out[0].format(tmp_path / "src")


def test_xmlns_and_schema_uris_ignored(tmp_path):
    text = (
        '<x xmlns="http://www.tibco.com/schemas/foo" xmlns:p="http://example.invalid/p"\n'
        ' a="http://www.w3.org/2001/XMLSchema" b="http://schemas.xmlsoap.org/soap/envelope/"/>\n'
    )
    assert audit(tmp_path, {"a.json": text, "b.md": text}) == []


def test_tibco_namespaces_ignored(tmp_path):
    text = 'NS = "http://xmlns.tibco.com/bw/process/2003" "http://tns.tibco.com/bw/model/core/bwext"'
    assert audit(tmp_path, {"a.py": text}) == []


def test_example_loopback_private_ignored(tmp_path):
    text = (
        'a = "http://example.com/x"\nb = "https://foo.example.org"\nc = "http://localhost:3000"\n'
        'd = "http://127.0.0.1:9000"\ne = "http://10.1.2.3/"\nf = "http://192.168.0.5"\n'
        'g = "http://172.20.0.1"\nh = "https://x.test/"\ni = "http://[::1]:80/"\n'
    )
    assert audit(tmp_path, {"a.py": text}) == []


def test_python_docstrings_and_comments_skipped_literals_checked(tmp_path):
    text = (
        '"""See https://docs.python.org/3/ for details."""\n'
        "# see https://github.com/foo/bar\n"
        "def f():\n"
        '    """Docs: https://readthedocs.io/x"""\n'
        '    return "https://api.openai.com/v1"\n'
    )
    out = audit(tmp_path, {"a.py": text})
    assert [(v.line, v.host) for v in out] == [(5, "api.openai.com")]


def test_python_bare_fqdn_in_string_literal(tmp_path):
    out = audit(tmp_path, {"a.py": 'HOST = "telemetry.vendor.io"\nNAME = "config.json"\n'})
    assert [v.host for v in out] == ["telemetry.vendor.io"]


def test_js_comments_skipped_but_urls_in_strings_kept(tmp_path):
    text = (
        "// see https://developer.mozilla.org/x\n"
        "/* https://docs.vendor.io/a\n   https://docs.vendor.io/b */\n"
        'const u = "https://api.vendor.io/v1"; // https://other.vendor.io\n'
        "const r = /['\"]/g, t = `https://${h}/x`;\n"
        'fetch("https://late.vendor.io/");\n'
    )
    out = audit(tmp_path, {"a.js": text})
    assert [(v.line, v.host) for v in out] == [(4, "api.vendor.io"), (6, "late.vendor.io")]


def test_single_label_and_local_names_ignored(tmp_path):
    text = 'a = "http://worker:9100/x"\nb = "http://svc.corp.local/"\n'
    assert audit(tmp_path, {"a.py": text}) == []


def test_tests_directories_skipped_for_python_only(tmp_path):
    out = audit(tmp_path, {"tests/t.py": 'u = "https://x.vendor.io"', "tests/t.js": 'u="https://y.vendor.io"'})
    assert [v.host for v in out] == ["y.vendor.io"]


def test_optional_section_needs_flag(tmp_path):
    files = {"a.py": 'URL = "https://api.anthropic.com/v1"'}
    assert [v.host for v in audit(tmp_path, files)] == ["api.anthropic.com"]
    assert audit(tmp_path, files, optional=("claude",)) == []


def test_allowlist_entries_must_be_commented(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text("# why\nok.example.io\n\nbad.example.io\n")
    with pytest.raises(ValueError, match="bad.example.io"):
        ea.load_allowlist(allow, (), require_comments=True)


def test_shipped_allowlist_is_commented():
    ea.load_allowlist(ALLOWLIST, ("claude",), require_comments=True)


def test_repo_engine_and_api_have_no_violations():
    allow = ea.load_allowlist(ALLOWLIST, ())
    found = ea.audit_dirs([REPO / "engine", REPO / "api"], allow)
    assert [v.format(REPO) for v in found] == []
