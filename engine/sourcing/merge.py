"""Hand edits into a fresh generation (sub-project 6, spec section 3, "Building from edits").

For each ``modified`` file a three-way merge: base = the baseline build's file, ours = the fresh
generation, theirs = the edited file (``git merge-file -p --diff3``). Fresh equal to base keeps
the edited file verbatim (``kept``); a clean merge is written (``merged``); a conflict keeps the
edited file verbatim and writes the merge output, both sides marked, to
``.exiqo/conflicts/<path>.merge`` (``conflict``): the engineer's edit is the newest human decision.
A file a model filled in the baseline (``ai``, or ``agent`` changed by a repair), or a person
edited in an earlier edit build (``hand``), merges against the baseline's file (X23): the edit is
kept, and flagged as a ``conflict`` when the design changed since the baseline. "Unchanged"
ignores layout (the baseline was formatted, the fresh generation not); a clean merge keeps the
edit's line endings; an ``added`` file over a path the generation now writes is a ``conflict``. ``added`` files are copied; ``deleted`` files are removed unless they are a required scaffold the
fresh generation still has (``restored``). Every outcome but ``deleted`` is a ``hand`` path the
agents leave alone (:func:`hand_paths`).

Changes are duck-typed (``path``, ``change``, ``origin``), so the engine does not depend on the
API's ``FileChange``. Each path is checked to stay inside the trees it reads and writes.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Collection, Iterable
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel

from engine.dotnet_gen.generator import GeneratedSolution, GenFile

MergeOutcome = Literal["kept", "merged", "conflict", "added", "deleted", "restored"]
CONFLICTS_DIR = ".exiqo/conflicts"
TMP_DIR = ".exiqo/tmp"
MERGE_TIMEOUT_S = 60
NO_BASELINE = "baseline folder missing: edit kept as uploaded"
MODEL_WRITTEN = "model-written in the baseline build: edit kept as uploaded"
HAND_WRITTEN = "hand-edited in the baseline build: edit kept as uploaded"
ADDED_OVER = ("# The generation now writes this path too: the added hand file was kept.\n"
              "# Below is what the generation wrote; carry its changes over by hand.")
_WS = re.compile(r"\s+")
SCAFFOLD_NOTE = ("# The design changed since the baseline build: the hand edit (a model-filled file) was kept.\n"
                 "# Below is the scaffold the new design generates for this file; carry its changes over by hand.")
#: conflict markers name the three sides
LABELS = ("generated", "baseline", "hand edit")


class MergeError(RuntimeError):
    """``git merge-file`` could not run (absent, timed out, or failed)."""


class MergeResult(BaseModel):
    path: str
    outcome: MergeOutcome
    detail: str = ""


def three_way(base: str, ours: str, theirs: str, git: str = "git", tmp_dir: Path | None = None) -> tuple[str, bool]:
    """``git merge-file -p --diff3 <ours> <base> <theirs>`` on temporary files (under ``tmp_dir``,
    e.g. the build's ``.exiqo/tmp``): ``(merged text, conflicted)``."""
    if tmp_dir is not None:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_dir) as d:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = Path(d) / name
            path.write_bytes(text.encode("utf-8"))
            paths.append(str(path))
        labels = [arg for label in LABELS for arg in ("-L", label)]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": d, "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"}
        try:
            proc = subprocess.run([git, "merge-file", "-p", "--diff3", *labels, *paths], capture_output=True,
                                  timeout=MERGE_TIMEOUT_S, check=False, env=env, cwd=d)
        except (OSError, subprocess.SubprocessError) as exc:
            raise MergeError(f"git merge-file could not run ({git}): {exc}") from exc
    # exit status: 0 clean, the number of conflicts (capped at 127), negative or > 127 an error
    if proc.returncode < 0 or proc.returncode > 127:
        raise MergeError(f"git merge-file failed: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout.decode("utf-8", "replace"), proc.returncode > 0


def _inside(root: Path, path: str) -> Path:
    rel = PurePosixPath(path)
    if not path or rel.is_absolute() or ".." in rel.parts or "\\" in path or ":" in rel.parts[0]:
        raise ValueError(f"{path} is outside the build")
    target = root / rel
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"{path} is outside the build")
    return target


def _read(root: Path | None, path: str) -> bytes | None:
    if root is None:
        return None
    target = _inside(root, path)
    if target.is_symlink() or not target.is_file():
        return None
    return target.read_bytes()


def _text(data: bytes) -> str | None:
    """``data`` as text with ``\\n`` line endings, ``None`` when it is not UTF-8 text."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


def _write(solution: GeneratedSolution, path: str, data: bytes) -> None:
    target = _inside(solution.root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    content = data.decode("utf-8", "replace")
    for i, f in enumerate(solution.files):
        if f.path == path:
            solution.files[i] = f.model_copy(update={"content": content})
            return
    solution.files.append(GenFile(path=path, content=content, origin="hand"))


def _conflict_record(solution: GeneratedSolution, path: str, text: str) -> str:
    record = f"{CONFLICTS_DIR}/{path}.merge"
    target = _inside(solution.root, record)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return record


def _same_code(a: str, b: str) -> bool:
    """Equal but for layout: the baseline folder went through ``dotnet format`` and a fresh
    generation has not, so indentation and line breaks alone are not a generated change."""
    return _WS.sub("", a) == _WS.sub("", b)


def _crlf_like(theirs: bytes, text: str) -> bytes:
    """``text`` with the edited file's line endings (CRLF when the edit used them)."""
    data = text.encode("utf-8")
    return data.replace(b"\n", b"\r\n") if b"\r\n" in theirs else data


def _modified(solution: GeneratedSolution, base_root: Path | None, path: str, origin: str, theirs: bytes,
              git: str, scaffold_changed: bool, model_written: bool) -> MergeResult:
    if origin in ("ai", "hand") or model_written:
        # X23: the merge base of a file a model (or, in an earlier edit build, a person) wrote is the
        # baseline's file, not the fresh scaffold (merging against that would put the placeholders
        # back). With the design unchanged that is a clean merge to the edit. With a new design the
        # scaffold may have moved under it: the edit is kept and flagged, never silently re-scaffolded.
        fresh_bytes = _read(solution.root, path)
        _write(solution, path, theirs)
        if not scaffold_changed:
            return MergeResult(path=path, outcome="kept", detail=HAND_WRITTEN if origin == "hand" else MODEL_WRITTEN)
        fresh = (fresh_bytes or b"").decode("utf-8", "replace")
        record = _conflict_record(solution, path, f"{SCAFFOLD_NOTE}\n{fresh}")
        return MergeResult(path=path, outcome="conflict", detail=record)
    base = _read(base_root, path) if base_root is not None and base_root.is_dir() else None
    if base is None:
        _write(solution, path, theirs)
        return MergeResult(path=path, outcome="kept", detail=NO_BASELINE)
    ours = _read(solution.root, path)
    if ours is None:
        _write(solution, path, theirs)
        return MergeResult(path=path, outcome="kept", detail="no longer generated: edit kept as uploaded")
    texts = [_text(b) for b in (base, ours, theirs)]
    unchanged = _same_code(texts[0], texts[1]) if texts[0] is not None and texts[1] is not None else ours == base
    if unchanged:
        _write(solution, path, theirs)  # the generation did not move: the edit verbatim
        return MergeResult(path=path, outcome="kept")
    if None in texts:
        _write(solution, path, theirs)
        return MergeResult(path=path, outcome="conflict", detail="not a text file: edit kept as uploaded")
    try:
        merged, conflicted = three_way(texts[0], texts[1], texts[2], git, solution.root / TMP_DIR)
    except MergeError as exc:
        _write(solution, path, theirs)
        return MergeResult(path=path, outcome="conflict", detail=f"{exc}: edit kept as uploaded")
    if conflicted:
        _write(solution, path, theirs)
        return MergeResult(path=path, outcome="conflict", detail=_conflict_record(solution, path, merged))
    _write(solution, path, _crlf_like(theirs, merged))
    return MergeResult(path=path, outcome="merged")


def apply_edits(solution: GeneratedSolution, baseline_root: Path | None, edited_root: Path,
                changes: Iterable[Any], required: set[str], git: str = "git",
                scaffold_changed: bool = False, model_written: Collection[str] = ()) -> list[MergeResult]:
    """Apply ``changes`` (``path``, ``change``, ``origin``) from ``edited_root`` to the fresh
    generation in ``solution`` (on disk and in ``solution.files``), one result per change in order.
    ``required`` are the scaffold paths (solution and project files) a deletion may not remove when
    the generation still has them. ``scaffold_changed``: the design (or spec) moved since the
    baseline build, so a model-filled file may sit on a different scaffold (X23). ``model_written``:
    baseline paths of origin ``agent`` that a model changed anyway (an applied repair), merged like
    ``ai`` files."""
    out: list[MergeResult] = []
    for c in changes:
        path, change = c.path, c.change
        if change == "deleted":
            target = _inside(solution.root, path)
            generated = target.is_file() and not target.is_symlink()
            if generated and c.origin == "code" and path in required:
                out.append(MergeResult(path=path, outcome="restored",
                                       detail="a required scaffold the generation still writes"))
                continue
            if generated:
                target.unlink()
            solution.files = [f for f in solution.files if f.path != path]
            out.append(MergeResult(path=path, outcome="deleted"))
            continue
        theirs = _read(edited_root, path)
        if theirs is None:
            raise ValueError(f"{path} is listed as {change} but is not in the edited tree")
        if change == "added":
            fresh = _read(solution.root, path)
            _write(solution, path, theirs)
            if fresh is not None and fresh != theirs:
                record = _conflict_record(solution, path, f"{ADDED_OVER}\n{fresh.decode('utf-8', 'replace')}")
                out.append(MergeResult(path=path, outcome="conflict", detail=record))
            else:
                out.append(MergeResult(path=path, outcome="added"))
        elif change == "modified":
            out.append(_modified(solution, baseline_root, path, c.origin, theirs, git, scaffold_changed,
                                  path in model_written))
        else:
            raise ValueError(f"{path}: unknown change {change!r}")
    return out


def hand_paths(results: Iterable[MergeResult]) -> set[str]:
    """The paths a person wrote: every outcome except ``deleted``."""
    return {r.path for r in results if r.outcome != "deleted"}
