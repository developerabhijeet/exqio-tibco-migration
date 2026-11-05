"""The platform entrypoint fails closed when MIRROR_CA_BUNDLE is set but cannot be used."""
from __future__ import annotations

import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "deploy" / "docker" / "entrypoint.sh"


def _run(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    script = tmp_path / "entrypoint.sh"
    script.write_text(ENTRYPOINT.read_text().replace("cd /app", "cd ."), encoding="utf-8")
    base = {"PATH": "/usr/bin:/bin", "EXIQO_ROLE": "nonsense"}  # an unknown role exits 64 if reached
    return subprocess.run(["sh", str(script)], check=False, env={**base, **env}, cwd=tmp_path,
                          capture_output=True, text=True, timeout=30)


def test_unset_bundle_changes_nothing(tmp_path):
    assert _run(tmp_path, {}).returncode == 64  # went on to the role check


def test_missing_bundle_file_fails_the_pod(tmp_path):
    r = _run(tmp_path, {"MIRROR_CA_BUNDLE": str(tmp_path / "absent.pem")})
    assert r.returncode == 66 and "absent.pem" in r.stderr


def test_missing_keytool_fails_the_pod(tmp_path):
    pem = tmp_path / "ca.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    r = _run(tmp_path, {"MIRROR_CA_BUNDLE": str(pem), "JAVA_HOME": str(tmp_path / "nojava")})
    assert r.returncode == 66 and "keytool" in r.stderr
