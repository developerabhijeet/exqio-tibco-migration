#!/usr/bin/env python3
"""Detect the external toolchain ExiQO Migrate validators rely on.

Search order for each tool: <repo>/.tools/bin, <repo>/.tools/dotnet, then PATH.
`terraform` may be satisfied by OpenTofu (symlinked as terraform in .tools/bin).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parents[1]

TOOLS: dict[str, list[str]] = {
    "dotnet": ["--version"],
    "terraform": ["version"],
    "terragrunt": ["--version"],
    "docker": ["--version"],
    "az": ["version", "--output", "tsv"],
    "node": ["--version"],
}


class ToolInfo(TypedDict):
    present: bool
    version: str | None
    path: str | None


def _local_dirs() -> list[str]:
    return [str(REPO_ROOT / ".tools" / "bin"), str(REPO_ROOT / ".tools" / "dotnet")]


def _find(name: str) -> str | None:
    local = shutil.which(name, path=os.pathsep.join(_local_dirs()))
    return local or shutil.which(name)


def _version(path: str, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or proc.stderr or "").strip()
    return out.splitlines()[0].strip() if out else None


def detect() -> dict[str, ToolInfo]:
    result: dict[str, ToolInfo] = {}
    for name, args in TOOLS.items():
        path = _find(name)
        if path is None:
            result[name] = {"present": False, "version": None, "path": None}
        else:
            result[name] = {"present": True, "version": _version(path, args), "path": path}
    return result


def main() -> int:
    info = detect()
    print(f"{'tool':<12}{'present':<9}{'version':<42}path")
    print("-" * 100)
    for name, i in info.items():
        print(
            f"{name:<12}{'yes' if i['present'] else 'NO':<9}"
            f"{(i['version'] or '-')[:40]:<42}{i['path'] or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
