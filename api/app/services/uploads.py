"""Upload storage: rebuild a zip entry by entry, or copy a server folder, under one upload folder.

Nothing from an archive is written before every entry has been checked. An entry is refused
(``UnsafePath``) when it is absolute (``/x``, ``\\x``, ``C:x``), has a ``..`` segment, is a
symlink, or resolves outside the target folder. Folder copies never follow symlinks.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Any

from engine.tibco_parser.scanner import SKIP_DIRS, file_kind

_CHUNK = 1024 * 1024
_DRIVE = re.compile(r"^[A-Za-z]:")
_DIALECT_BY_KIND = {"bwp": "bw6", "bwm": "bw6", "process": "bw5"}


class UploadError(ValueError):
    """A client error in the upload (bad archive, missing path, too large)."""


class UnsafePath(UploadError):
    def __init__(self, entry: str) -> None:
        super().__init__(f"unsafe path: {entry}")
        self.entry = entry


def _entry_parts(info: zipfile.ZipInfo, dest: Path) -> list[str]:
    """Validated path segments of one archive entry (empty for the archive root)."""
    name = info.filename
    norm = name.replace("\\", "/")
    if not name or "\x00" in name or norm.startswith("/") or _DRIVE.match(norm):
        raise UnsafePath(name)
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UnsafePath(name)
    if stat.S_ISLNK(info.external_attr >> 16):
        raise UnsafePath(name)
    if parts and not dest.joinpath(*parts).resolve().is_relative_to(dest.resolve()):
        raise UnsafePath(name)
    return parts


def _copy_stream(src, dst_path: Path, budget: list[int]) -> None:
    with open(dst_path, "wb") as dst:
        while chunk := src.read(_CHUNK):
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise UploadError("upload is larger than the configured limit")
            dst.write(chunk)


def extract_zip(archive: Path, dest: Path, max_bytes: int) -> None:
    """Rebuild ``archive`` under ``dest`` path by path after checking every entry."""
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UploadError("not a zip file") from exc
    with zf:
        plan = [(info, _entry_parts(info, dest)) for info in zf.infolist()]  # refuse before writing
        dest.mkdir(parents=True, exist_ok=True)
        budget = [max_bytes]
        try:
            for info, parts in plan:
                if not parts or parts[0] in SKIP_DIRS:
                    continue
                target = dest.joinpath(*parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src:
                    _copy_stream(src, target, budget)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise UploadError(f"cannot extract archive: {exc}") from exc


def copy_folder(src: Path, dest: Path, max_bytes: int) -> None:
    """Copy a server folder (regular files only, symlinks skipped) under ``dest``."""
    if dest.resolve().is_relative_to(src.resolve()):
        raise UploadError("path contains the upload store")
    budget = [max_bytes]
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not (here / d).is_symlink())
        out = dest / here.relative_to(src)
        out.mkdir(parents=True, exist_ok=True)
        for fn in sorted(filenames):
            p = here / fn
            if p.is_symlink() or not p.is_file():
                continue
            with open(p, "rb") as fh:
                _copy_stream(fh, out / fn, budget)


def single_root(dest: Path) -> str:
    """The relative folder to scan: the archive's single top folder when there is exactly one."""
    entries = [p for p in dest.iterdir() if p.name not in SKIP_DIRS]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0].name
    return ""


def inventory(root: Path) -> list[dict[str, Any]]:
    """Every file under ``root`` (sorted by path) with kind, provisional dialect, sha256 and size."""
    out: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            digest = hashlib.sha256()
            with open(p, "rb") as fh:
                while chunk := fh.read(_CHUNK):
                    digest.update(chunk)
            kind = file_kind(p)
            out.append({"path": p.relative_to(root).as_posix(), "kind": kind,
                        "dialect": _DIALECT_BY_KIND.get(kind), "sha256": digest.hexdigest(),
                        "size": p.stat().st_size})
    return sorted(out, key=lambda e: e["path"])


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
