"""The private CA of GMF's mirrors, added to the system roots.

Go and uv read one file (``SSL_CERT_FILE``) and trust nothing else, so pointing it at the mirror CA
alone would break a second mirror with a public certificate. The file handed to them is the system
roots followed by the mirror CA (the Java build gets the same effect from its trust-store copy).
"""
from __future__ import annotations

import hashlib
import os
import ssl
import tempfile
from pathlib import Path

CACHE_DIR = Path(tempfile.gettempdir()) / "exiqo-ca"
_COMMON = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem")


def system_bundle() -> Path | None:
    """The system CA file (OpenSSL's default location, else the usual distribution paths)."""
    paths = ssl.get_default_verify_paths()
    for candidate in (os.environ.get("SSL_CERT_FILE"), paths.cafile, paths.openssl_cafile, *_COMMON):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def with_system_roots(mirror_ca: Path | str) -> str:
    """Path of a PEM holding the system roots and then ``mirror_ca``; ``mirror_ca`` itself when there
    are no system roots to add to. The file is named by the content hash, so it is written once."""
    ca = Path(mirror_ca)
    system = system_bundle()
    if system is None:
        return str(ca)
    data = system.read_bytes().rstrip(b"\n") + b"\n" + ca.read_bytes().rstrip(b"\n") + b"\n"
    target = CACHE_DIR / f"roots-{hashlib.sha256(data).hexdigest()[:16]}.pem"
    if not target.is_file():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return str(target)
