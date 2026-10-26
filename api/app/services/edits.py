"""Hand edits against a build (sub-project 6, spec section 3).

The baseline: every download carries ``EXIQO_BASELINE.json`` naming the build it came from and the
``text_sha256`` of every file in the zip, so an edited copy can be compared with it later. The
manifest only identifies the build; what counts as the baseline is the server's own build folder
and ``build_files`` rows (a zip is user-edited data, its manifest included).
"""
from __future__ import annotations

import hashlib
from typing import Any

from api.app.models import Build, Process
from api.app.services.build_tree import download_excluded as stack_download_excluded

BASELINE_NAME = "EXIQO_BASELINE.json"
NOTE_NAME = "EXPORTED_FROM.md"
BASELINE_FORMAT = 1
#: The stack a build without one (before sub-project 5) was generated for.
DEFAULT_STACK = "dotnet"


def build_stack(build: Build) -> str:
    return getattr(build, "stack", None) or DEFAULT_STACK


def download_excluded(build: Build) -> frozenset[str]:
    """The folders a build's download (and the edit diff) leaves out: its stack's
    ``download_excluded``."""
    return stack_download_excluded(build.stack)


def text_sha256(data: bytes) -> str:
    """sha256 of ``data`` with CRLF read as LF when it is text (UTF-8 without a NUL byte), so a
    checkout or an editor that rewrote the line endings is not an edit; raw bytes otherwise."""
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def baseline_manifest(build: Build, process: Process | None, files: dict[str, str]) -> dict[str, Any]:
    """``EXIQO_BASELINE.json`` for ``build``: ``files`` maps each zipped path to its ``text_sha256``."""
    return {"format": BASELINE_FORMAT, "project_id": process.project_id if process else None,
            "process_id": build.process_id, "build_id": build.id, "stack": build_stack(build),
            "build_hash": build.build_hash, "design_hash": build.design_hash, "iteration": build.iteration,
            "files": dict(sorted(files.items()))}
