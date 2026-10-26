"""Failures collected for an iteration (sub-project 6): failed build steps and compiler errors,
failing tests, parity differences nobody explained, a failed plan, and uploaded error logs.

Everything here is data for a later prompt, never instructions: text is masked
(``mask_sensitive``) before it is stored or used, stripped of control characters and of literal
``[S<n>]`` citation markers, and cut to an excerpt. Uploaded logs are untrusted input: size
capped, text only, never executed or rendered, stored under safe names."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.models import Build, FieldDiff, Plan, Scenario, ScenarioRun, TestResult
from api.app.services.text_safety import MASKED_FIELDS, one_line
from api.app.services.uploads import UploadError
from engine.agents.provider import mask_credentials, mask_sensitive
from engine.memory.model import KnowledgeError, guard_text

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
EXCERPT_LIMIT = 8000
TITLE_LIMIT = 200
STEP_LOG_LINES = 60
COMPILER_ERRORS = 25
MAX_TESTS = 100
MAX_ITEMS = 100

_MATCH = re.compile(r"(?i)error|exception|fail|fatal|denied|refused|timeout")
_COMPILER = re.compile(r"(?i)\berror\s+(?:CS|MSB|NU)\d+|:\s*error\b|\[ERROR\]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")
_MARKER = re.compile(r"\[S\d+\]")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class LogTooLarge(UploadError):
    """An uploaded log over ``MAX_UPLOAD_BYTES`` (HTTP 413; other refusals are 422)."""


class FailureItem(BaseModel):
    kind: Literal["build_failure", "test_failure", "parity_defect", "plan_error", "uploaded_log"]
    ref: str
    title: str
    text: str
    process_id: str
    sha256: str | None = None
    # the whole masked log of an upload, for store_log; never serialised
    full_text: str | None = Field(default=None, exclude=True, repr=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        return f"{self.kind}:{self.ref}:{hashlib.sha1(self.title.encode()).hexdigest()[:8]}"


def clean(text: str) -> str:
    """``text`` without control characters (newlines and tabs stay) or literal citation markers,
    masked. Control characters go first so one inside a secret cannot dodge the masker; the
    markers are stripped until none is left (``[[S1]S2]`` must not leave ``[S2]``)."""
    out = mask_sensitive(mask_credentials(_CONTROL.sub("", text or "")), MASKED_FIELDS)
    while True:
        stripped = _MARKER.sub("", out)
        if stripped == out:
            return mask_sensitive(mask_credentials(out), MASKED_FIELDS)
        out = stripped


def _cap(text: str, limit: int) -> str:
    """Head and tail of ``text`` when it is longer than ``limit``, with a note of what is missing."""
    if len(text) <= limit:
        return text
    keep = max(limit - 60, 0) // 2
    omitted = len(text) - 2 * keep
    return f"{text[:keep]}\n[… {omitted} characters omitted …]\n{text[len(text) - keep:]}"


def _gap(n: int) -> str:
    return f"[… {n} line{'s' if n != 1 else ''} omitted …]"


def excerpt(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """The lines that matter in a log: those mentioning an error with two lines of context,
    overlapping ranges merged and each gap noted as ``[… N lines omitted …]``. A log without any
    gives its last 200 lines. Cut to ``limit`` characters, keeping head and tail."""
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if _MATCH.search(line)]
    if not hits:
        tail = lines[-200:]
        head = [_gap(len(lines) - len(tail))] if len(lines) > len(tail) else []
        return _cap("\n".join(head + tail), limit)
    ranges: list[list[int]] = []
    for i in hits:
        lo, hi = max(0, i - 2), min(len(lines) - 1, i + 2)
        if ranges and lo <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], hi)
        else:
            ranges.append([lo, hi])
    out: list[str] = []
    last = -1
    for lo, hi in ranges:
        if lo > last + 1:
            out.append(_gap(lo - last - 1))
        out.extend(lines[lo : hi + 1])
        last = hi
    if last < len(lines) - 1:
        out.append(_gap(len(lines) - 1 - last))
    return _cap("\n".join(out), limit)


def _title(value: str) -> str:
    return one_line(value, TITLE_LIMIT) or "(untitled)"


def read_log_upload(name: str, data: bytes) -> FailureItem:
    """A log file from an upload as an item. Raises ``UploadError`` for a file that is too large,
    binary, empty or raw TIBCO process XML."""
    shown = _title(Path(str(name).replace("\\", "/")).name)
    if len(data) > MAX_UPLOAD_BYTES:
        raise LogTooLarge(f"{shown} is larger than 5 MB")
    if b"\x00" in data[:8192]:
        raise UploadError(f"{shown} looks binary: only text logs are accepted")
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            raw = data.decode("cp1252")
        except UnicodeDecodeError:  # the five bytes cp1252 leaves undefined
            raw = data.decode("latin-1")
    try:
        guard_text(raw)
    except KnowledgeError as exc:
        if not raw.strip():
            raise UploadError(f"{shown} is empty") from exc
        raise UploadError(f"{shown} contains raw TIBCO process XML, which is not accepted as a log") from exc
    full = clean(raw)
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()
    return FailureItem(kind="uploaded_log", ref=shown, title=shown, text=excerpt(full), process_id="",
                       sha256=digest, full_text=full)


def store_log(settings: Settings, iteration_id: str, item: FailureItem) -> Path:
    """Write the masked log to ``DATA_DIR/iterations/<id>/<sha256>.log``. The name comes from the
    content hash, never from the upload's own name."""
    if not _SAFE_ID.match(iteration_id):
        raise ValueError("unsafe iteration id")
    if not item.sha256 or not _SHA.match(item.sha256):
        raise ValueError("an uploaded log needs its sha256")
    root = (settings.data_dir.resolve() / "iterations").resolve()
    folder = (root / iteration_id).resolve()
    if folder.parent != root:
        raise ValueError("unsafe iteration id")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{item.sha256}.log"
    path.write_text(item.full_text if item.full_text is not None else item.text, encoding="utf-8")
    return path


def _tail(text: str, n: int) -> str:
    """The last ``n`` lines of ``text`` (the first of them a note when some are left out)."""
    lines = text.splitlines()
    if len(lines) <= n:
        return "\n".join(lines)
    return "\n".join([_gap(len(lines) - (n - 1)), *lines[-(n - 1):]])


def _dedupe_and_cap(items: list[FailureItem]) -> list[FailureItem]:
    """One item per key (the first wins) and at most ``MAX_ITEMS``; a plan error is kept over the
    tail of the list."""
    seen: set[str] = set()
    unique = [i for i in items if not (i.key in seen or seen.add(i.key))]
    plans = [i for i in unique if i.kind == "plan_error"]
    rest = [i for i in unique if i.kind != "plan_error"]
    return rest[: MAX_ITEMS - len(plans)] + plans


async def collect_failures(session: AsyncSession, process_id: str) -> list[FailureItem]:
    """What failed in the latest build of ``process_id``: failed steps (last 60 log lines), the
    first 25 compiler errors, failing tests, scenario runs that differ or error (with their
    unexplained field differences) and the latest plan when it failed."""
    build = await session.scalar(
        select(Build).where(Build.process_id == process_id).order_by(Build.created_at.desc(), Build.id.desc()).limit(1)
    )
    if build is None:
        return []
    items: list[FailureItem] = []

    def add(kind: str, ref: str, title: str, text: str) -> None:
        items.append(FailureItem(kind=kind, ref=ref, title=_title(title), process_id=process_id,
                                 text=_cap(clean(text), EXCERPT_LIMIT)))

    for step in build.steps or []:
        if step.get("ok") is not False:
            continue
        name = str(step.get("name") or "step")
        log = str(step.get("log") or "")
        add("build_failure", build.id, name, _tail(log, STEP_LOG_LINES) or "(no log)")
        errors = [ln for ln in log.splitlines() if _COMPILER.search(ln)][:COMPILER_ERRORS]
        if errors:
            add("build_failure", build.id, f"{name}: compiler errors", "\n".join(errors))
    if build.error and not any(i.kind == "build_failure" for i in items):
        add("build_failure", build.id, "build error", excerpt(build.error))

    tests = (await session.scalars(
        select(TestResult).where(TestResult.build_id == build.id, TestResult.outcome == "failed")
        .order_by(TestResult.name).limit(MAX_TESTS)
    )).all()
    for t in tests:
        add("test_failure", t.name, t.display_name or t.name, t.message or "(no message)")

    runs = (await session.execute(
        select(ScenarioRun, Scenario.key).join(Scenario, Scenario.id == ScenarioRun.scenario_id)
        .where(ScenarioRun.build_id == build.id, ScenarioRun.status.in_(("differs", "error")))
        .order_by(Scenario.key, ScenarioRun.created_at.desc())
    )).all()
    seen: set[str] = set()
    for run, key in runs:
        if run.scenario_id in seen:  # the newest run of each scenario only
            continue
        seen.add(run.scenario_id)
        diffs = (await session.scalars(
            select(FieldDiff).where(FieldDiff.run_id == run.id, FieldDiff.classification == "defect")
            .order_by(FieldDiff.field)
        )).all()
        lines = [f"{d.field}: expected {d.expected!r}, actual {d.actual!r}" + (f" ({d.note})" if d.note else "")
                 for d in diffs]
        if not lines:
            lines = [f"status: {run.status}"] + ([str(run.report.get("error"))] if run.report.get("error") else [])
        add("parity_defect", run.id, key, "\n".join(lines))

    plan = await session.scalar(
        select(Plan).where(Plan.build_id == build.id).order_by(Plan.created_at.desc(), Plan.id.desc()).limit(1)
    )
    if plan is not None and plan.status == "failed":
        add("plan_error", plan.id, "infrastructure plan", excerpt(plan.output or "") or "(no output)")
    return _dedupe_and_cap(items)
