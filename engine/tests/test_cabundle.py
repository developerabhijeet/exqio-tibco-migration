"""The mirror CA is added to the system roots for Go and uv, never substituted for them."""
from __future__ import annotations

import os
from pathlib import Path

from engine.stacks import cabundle


def _pem(tag: str) -> str:
    return f"-----BEGIN CERTIFICATE-----\n{tag}\n-----END CERTIFICATE-----\n"


def test_bundle_is_the_system_roots_plus_the_mirror_ca(tmp_path, monkeypatch):
    system, ca = tmp_path / "system.pem", tmp_path / "ca.pem"
    system.write_text(_pem("SYSTEM"))
    ca.write_text(_pem("MIRROR"))
    monkeypatch.setattr(cabundle, "system_bundle", lambda: system)
    monkeypatch.setattr(cabundle, "CACHE_DIR", tmp_path / "cache")
    out = cabundle.with_system_roots(ca)
    text = Path(out).read_text()
    assert "SYSTEM" in text and "MIRROR" in text and text.index("SYSTEM") < text.index("MIRROR")
    assert out == cabundle.with_system_roots(ca)  # the same file for the same inputs


def test_a_changed_ca_gets_a_new_file(tmp_path, monkeypatch):
    system, ca = tmp_path / "system.pem", tmp_path / "ca.pem"
    system.write_text(_pem("SYSTEM"))
    monkeypatch.setattr(cabundle, "system_bundle", lambda: system)
    monkeypatch.setattr(cabundle, "CACHE_DIR", tmp_path / "cache")
    ca.write_text(_pem("ONE"))
    first = cabundle.with_system_roots(ca)
    ca.write_text(_pem("TWO"))
    assert cabundle.with_system_roots(ca) != first


def test_without_system_roots_the_mirror_ca_alone_is_returned(tmp_path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text(_pem("MIRROR"))
    monkeypatch.setattr(cabundle, "system_bundle", lambda: None)
    assert cabundle.with_system_roots(ca) == str(ca)


def test_system_bundle_finds_a_file_on_this_machine():
    found = cabundle.system_bundle()
    assert found is None or os.path.isfile(found)
