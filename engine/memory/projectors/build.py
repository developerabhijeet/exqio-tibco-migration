"""Build projector: the generated solution as new-code nodes, its lineage from the old code,
and the Errors of failed steps and tests."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from engine.dotnet_gen.models import DesignRecord, MappingRow
from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.analysis import ProcessRef
from engine.memory.projectors.estate import _clean

STACK = "dotnet"
MESSAGE_MAX = 500
_COMPILER_AT = re.compile(r"([\w./-]+\.cs)\((\d+),(\d+)\)")


class BuildFileInput(BaseModel):
    path: str  # relative to the solution root
    origin: str
    sha256: str = ""
    size: int = 0


class TestInput(BaseModel):
    __test__ = False  # not a pytest class

    name: str
    kind: str
    rule_key: str | None = None
    outcome: str
    duration_ms: int = 0
    message: str = ""


class StepInput(BaseModel):
    name: str
    ok: bool
    log: str = ""


class BuildInput(BaseModel):
    build_id: str
    build_hash: str = ""
    stack: str = STACK
    design: DesignRecord
    files: list[BuildFileInput] = []
    tests: list[TestInput] = []
    steps: list[StepInput] = []
    starter_activity_id: str | None = None
    starter_kind: str | None = None
    starter_config: dict[str, Any] = {}
    app_id: str = ""  # IR application id, the prefix of ``substvar:<app id>/<name>``


def _error(uid: str, name: str, kind: str, message: str, build_id: str) -> MemNode:
    message = message[:MESSAGE_MAX]
    return MemNode(label="Error", uid=uid, name=name, text=message or name,
                   props={"kind": kind, "message": message, "artifact_type": "build",
                          "artifact_id": build_id})


def project_build(ref: ProcessRef, b: BuildInput) -> ScopedDelta:
    ir, stack, design = ref.ir_id, b.stack, b.design
    nodes: dict[str, MemNode] = {}
    rels: dict[str, MemRel] = {}

    def add_node(node: MemNode) -> MemNode:
        return nodes.setdefault(node.uid, node)

    def add_rel(rtype: str, src: str, dst: str, props: dict[str, Any] | None = None) -> None:
        rel = MemRel(type=rtype, src=src, dst=dst, props=props or {})  # type: ignore[arg-type]
        rels.setdefault(rel.uid, rel)

    def new(label: str, uid: str, name: str, text: str = "", *, path: str | None = None,
            artifact_type: str = "build_file", **props: Any) -> MemNode:
        return add_node(MemNode(label=label, uid=uid, name=name, text=text,  # type: ignore[arg-type]
                                props=_clean({"stack": stack, "artifact_type": artifact_type,
                                              "artifact_id": b.build_id, "process_id": ref.api_id,
                                              "path": path, **props})))

    solution = f"solution:{stack}:{ir}"
    new("Solution", solution, design.solution.name,
        f"Solution {design.solution.name} generated for {ref.name}", artifact_type="build",
        build_id=b.build_id, build_hash=b.build_hash)
    projects = design.solution.projects
    api = next((p for p in projects if p.kind == "api"), projects[0] if projects else None)
    api_uid = f"codeproject:{stack}:{ir}/{api.name}" if api else None
    core = next((p for p in projects if p.kind == "core"), None)

    project_uid: dict[str, str] = {}
    for p in projects:
        uid = f"codeproject:{stack}:{ir}/{p.name}"
        project_uid[p.name] = uid
        new("CodeProject", uid, p.name, artifact_type="build", kind=p.kind)
        add_rel("CONTAINS", solution, uid)

    file_uids: dict[str, str] = {}
    for f in b.files:
        uid = f"codefile:{stack}:{ir}/{f.path}"
        file_uids[f.path] = uid
        new("CodeFile", uid, f.path.rsplit("/", 1)[-1], f.path, path=f.path, origin=f.origin,
            sha256=f.sha256, size=f.size)
        owner = max((n for n in project_uid if f.path.startswith(n + "/")), key=len, default=None)
        add_rel("CONTAINS", project_uid[owner] if owner else solution, uid)

    def add_class(project: str, cls: str, file: str, text: str) -> str:
        uid = f"class:{stack}:{ir}/{project}/{cls}"
        full = f"{project}/{file}" if file else ""
        new("Class", uid, cls, text, path=full or None, file=file or None)
        if full in file_uids:
            add_rel("CONTAINS", file_uids[full], uid)
        return uid

    def method_uid(r: MappingRow) -> str:
        return f"method:{stack}:{ir}/{r.project}/{r.class_name}.{r.method}"

    rows_by_class: dict[tuple[str, str], list[MappingRow]] = {}
    for r in design.mapping:
        rows_by_class.setdefault((r.project, r.class_name), []).append(r)
    method_of: dict[str, str] = {}
    class_of: dict[str, str] = {}
    for (project, cls), rows in rows_by_class.items():
        names = ", ".join(r.activity_name for r in rows)
        cuid = add_class(project, cls, rows[0].file,
                         f"Class {cls} in {project}, implements {ref.name} activities: {names}")
        for r in rows:
            muid = method_uid(r)
            new("Method", muid, r.method,
                f"{cls}.{r.method} implements {r.activity_name} ({r.canonical_type}) "
                f"using {r.pattern}", path=f"{project}/{r.file}", pattern=r.pattern,
                origin=r.origin)
            add_rel("CONTAINS", cuid, muid)
            add_rel("MIGRATED_TO", f"activity:{ir}/{r.activity_id}", muid,
                    {"status": "generated", "pattern": r.pattern, "origin": r.origin})
            method_of[r.activity_id] = muid
            class_of[r.activity_id] = cuid

    for c in design.contracts:
        if core is None:
            break
        for record in c.record_names:
            cuid = add_class(core.name, record, c.file, f"Record {record} for contract {c.contract_id}")
            add_rel("MIGRATED_TO", f"contract:{c.contract_id}", cuid,
                    {"status": "generated", "origin": "code"})

    for cfg in design.config:
        kuid = f"configkey:{stack}:{ir}/{cfg.key}"
        new("ConfigKey", kuid, cfg.key, f"Configuration key {cfg.key}", artifact_type="build",
            secret=cfg.secret)
        if api_uid:
            add_rel("USES", api_uid, kuid)
        if b.app_id and not cfg.substvar.startswith("resource:"):
            add_rel("MIGRATED_TO", f"substvar:{b.app_id}/{cfg.substvar}", kuid,
                    {"status": "generated", "origin": "code"})

    starter_method = method_of.get(b.starter_activity_id or "")
    for m in design.messaging:
        muid = f"messaging:{stack}:{ir}/{m.kind}/{m.name}"
        new("MessagingEntity", muid, m.name, f"{m.kind} {m.name} ({m.direction})",
            artifact_type="build", kind=m.kind, direction=m.direction)
        if starter_method:
            add_rel("CONSUMES" if m.direction == "in" else "PUBLISHES", starter_method, muid)

    if api_uid:
        add_rel("MIGRATED_TO", f"process:{ir}", api_uid, {"status": "generated", "origin": "code"})
    starter_class = class_of.get(b.starter_activity_id or "")
    if starter_class:
        add_rel("MIGRATED_TO", f"process:{ir}", starter_class,
                {"status": "generated", "origin": "code"})

    path = str(b.starter_config.get("path") or "")
    if b.starter_kind in ("rest", "http") and path and starter_method:
        verb = str(b.starter_config.get("verb") or "POST").upper()
        euid = f"endpoint:new:{verb} {path}"
        add_node(MemNode(label="Endpoint", uid=euid, name=f"{verb} {path}",
                         text=f"{verb} {path} served by {ref.name}",
                         props={"method": verb, "path": path, "side": "new", "stack": stack,
                                "artifact_type": "build", "artifact_id": b.build_id,
                                "process_id": ref.api_id}))
        add_rel("EXPOSES", starter_method, euid)
        add_rel("MIGRATED_TO", f"endpoint:old:{verb} {path}", euid,
                {"status": "generated", "origin": "code"})

    tested: dict[str, list[str]] = {}
    for t in b.tests:
        tuid = f"test:{stack}:{ir}/{t.name}"
        new("Test", tuid, t.name, f"Test {t.name} ({t.kind}): {t.outcome}", artifact_type="build",
            kind=t.kind, rule_key=t.rule_key, outcome=t.outcome, duration_ms=t.duration_ms,
            message=t.message[:MESSAGE_MAX] or None)
        tested[tuid] = []
        if t.rule_key:
            for r in design.mapping:
                if t.rule_key in r.rule_ids and method_uid(r) not in tested[tuid]:
                    tested[tuid].append(method_uid(r))
        for muid in tested[tuid]:
            add_rel("TESTS", tuid, muid)

    for s in b.steps:
        if s.ok:
            continue
        euid = f"error:build:{ir}:{s.name}"
        add_node(_error(euid, f"build step {s.name} failed", "build", s.log, b.build_id))
        target = None
        for m in _COMPILER_AT.finditer(s.log):
            found = m.group(1)
            target = next((u for p, u in file_uids.items()
                           if found == p or found.endswith("/" + p)), None)
            if target is None and not found.startswith("/"):
                target = f"codefile:{stack}:{ir}/{found.removeprefix('./')}"
            if target:
                break
        if target:
            add_rel("FAILED_AT", euid, target)
        elif api_uid:
            add_rel("ABOUT", euid, api_uid)

    for t in b.tests:
        if t.outcome != "failed":
            continue
        tuid = f"test:{stack}:{ir}/{t.name}"
        euid = f"error:test:{ir}:{t.name}"
        add_node(_error(euid, f"test {t.name} failed", "test", t.message, b.build_id))
        for muid in tested[tuid]:
            add_rel("FAILED_AT", euid, muid)
        if not tested[tuid] and api_uid:
            add_rel("ABOUT", euid, api_uid)

    return ScopedDelta(scope=f"build:{ir}", nodes=list(nodes.values()), rels=list(rels.values()))
