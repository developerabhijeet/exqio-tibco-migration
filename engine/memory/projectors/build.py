"""Build projector: the generated solution as new-code nodes, its lineage from the old code,
and the Errors of failed steps and tests."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from engine.domains.graph import endpoint_of
from engine.dotnet_gen.models import DesignRecord, MappingRow
from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.analysis import ProcessRef
from engine.memory.projectors.util import MESSAGE_MAX, clean, error_node

STACK = "dotnet"
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
                                props=clean({"stack": stack, "artifact_type": artifact_type,
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
        owner = max((n for n in project_uid if f"/{n}/" in f"/{f.path}"), key=len, default=None)
        add_rel("CONTAINS", project_uid[owner] if owner else solution, uid)

    def file_path(project: str, file: str) -> str | None:
        """The solution-relative path of a project's file: the real build file when one ends
        with ``<project>/<file>`` (the generator writes ``src/<project>/<file>``), else that
        layout."""
        if not file:
            return None
        tail = f"{project}/{file}"
        return next((p for p in file_uids if p == tail or p.endswith("/" + tail)),
                    f"src/{tail}")

    def add_class(project: str, cls: str, file: str, text: str) -> str:
        uid = f"class:{stack}:{ir}/{project}/{cls}"
        full = file_path(project, file)
        new("Class", uid, cls, text, path=full, file=file or None)
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
                f"using {r.pattern}", path=file_path(project, r.file), pattern=r.pattern,
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
        if starter_method:  # the entry Method anchors messaging: the design does not say more
            add_rel("CONSUMES" if m.direction == "in" else "PUBLISHES", starter_method, muid)

    if api_uid:
        add_rel("MIGRATED_TO", f"process:{ir}", api_uid, {"status": "generated", "origin": "code"})
    starter_class = class_of.get(b.starter_activity_id or "")
    if starter_class:
        add_rel("MIGRATED_TO", f"process:{ir}", starter_class,
                {"status": "generated", "origin": "code"})

    if b.starter_kind in ("rest", "http", "soap") and starter_method:
        cfg = b.starter_config  # as gen_controller reads it
        verb = str(cfg.get("verb") or "POST").upper()
        path = str(cfg.get("path") or cfg.get("resource_path") or f"/{ref.name}")
        euid = f"endpoint:new:{verb} {path}"
        add_node(MemNode(label="Endpoint", uid=euid, name=f"{verb} {path}",
                         text=f"{verb} {path} served by {ref.name}",
                         props={"method": verb, "path": path, "side": "new", "stack": stack,
                                "artifact_type": "build", "artifact_id": b.build_id,
                                "process_id": ref.api_id}))
        add_rel("EXPOSES", starter_method, euid)
        old = endpoint_of(b.starter_kind, cfg, ref.name)  # the estate graph's own naming
        if old:
            add_rel("MIGRATED_TO", f"endpoint:old:{old[0]}", euid,
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
        add_node(error_node(euid, f"build step {s.name} failed", "build", s.log,
                            artifact_type="build", artifact_id=b.build_id, process_id=ref.api_id))
        target = None
        for m in _COMPILER_AT.finditer(s.log):  # the first path that is a file of this build
            found = m.group(1).removeprefix("./")
            target = next((u for p, u in file_uids.items()
                           if found == p or found.endswith("/" + p) or p.endswith("/" + found)),
                          None)
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
        add_node(error_node(euid, f"test {t.name} failed", "test", t.message,
                            artifact_type="build", artifact_id=b.build_id, process_id=ref.api_id))
        for muid in tested[tuid]:
            add_rel("FAILED_AT", euid, muid)
        if not tested[tuid] and api_uid:
            add_rel("ABOUT", euid, api_uid)

    return ScopedDelta(scope=f"build:{ir}", nodes=list(nodes.values()), rels=list(rels.values()))
