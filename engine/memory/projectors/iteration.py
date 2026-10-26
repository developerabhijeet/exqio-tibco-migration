"""Iteration projector (sub-project 6): what an iteration brought in, as one revision.

Scope ``iteration:<n>``: per process, the updated requirements as a ``Chunk`` (``kind =
"requirement"``, uid ``chunk:<ir process id>:requirement:<n>``) and each input (failed build step,
failing test, parity defect, plan error, uploaded log) as an ``Error``
(uid ``error:iteration:<ir process id>:<input id>``), all ``ABOUT`` the process; an Error whose
input names a file of the build is also ``FAILED_AT`` that ``CodeFile``. An edit set's file list
becomes a ``Memory`` note (``source = "edit set"``) ``ABOUT`` each changed ``CodeFile``.

Every node carries ``iteration`` (``n``), which retrieval orders on (``prefer_iteration``).
"""
from __future__ import annotations

from pydantic import BaseModel

from engine.agents.provider import mask_credentials, mask_sensitive
from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.build import STACK
from engine.memory.projectors.util import MESSAGE_MAX, clean, error_node

#: iteration input kind -> ``Error.kind`` (the phases' own kinds, plus ``uploaded_log``)
ERROR_KIND: dict[str, str] = {"build_failure": "build", "test_failure": "test", "parity_defect": "parity",
                              "plan_error": "deploy", "uploaded_log": "uploaded_log"}


class IterationInputRef(BaseModel):
    id: str
    kind: str  # an ``iteration_inputs.kind``
    title: str
    text: str = ""  # the masked excerpt
    ref: str | None = None
    path: str | None = None  # a file of the build the input names, relative to the solution root


class IterationProcess(BaseModel):
    ir_id: str
    api_id: str
    requirements: str = ""
    inputs: list[IterationInputRef] = []


class EditedFile(BaseModel):
    path: str
    change: str  # modified | added | deleted


class IterationIngest(BaseModel):
    project_id: str
    n: int
    iteration_id: str
    processes: list[IterationProcess] = []
    edit_set: list[EditedFile] | None = None  # the files of the first process's edit set
    stack: str = STACK


#: Field names masked on top of the write path's own masking (it masks ``SENSITIVE_FIELDS`` only):
#: requirements and logs are free text people paste secrets into.
SECRET_FIELDS = ("password", "pwd", "secret", "token", "api_key", "access_key", "authorization")


def secret_free(text: str) -> str:
    # the credential first: masking the ``authorization`` field would hide the scheme it follows
    return mask_sensitive(mask_credentials(text or ""), SECRET_FIELDS)


def requirement_uid(ir_id: str, n: int) -> str:
    return f"chunk:{ir_id}:requirement:{n}"


def project_iteration(meta: IterationIngest) -> list[ScopedDelta]:
    nodes: dict[str, MemNode] = {}
    rels: dict[str, MemRel] = {}

    def add_rel(rtype: str, src: str, dst: str) -> None:
        rel = MemRel(type=rtype, src=src, dst=dst)  # type: ignore[arg-type]
        rels.setdefault(rel.uid, rel)

    def codefile(ir_id: str, path: str) -> str:
        return f"codefile:{meta.stack}:{ir_id}/{path}"

    for p in meta.processes:
        process_uid = f"process:{p.ir_id}"
        common = {"artifact_type": "iteration", "artifact_id": meta.iteration_id, "process_id": p.api_id,
                  "iteration": meta.n}
        if p.requirements.strip():
            uid = requirement_uid(p.ir_id, meta.n)
            nodes[uid] = MemNode(label="Chunk", uid=uid, name=f"requirements of iteration {meta.n}",
                                 text=secret_free(p.requirements), props={"kind": "requirement", **common})
            add_rel("ABOUT", uid, process_uid)
        for i in p.inputs:
            uid = f"error:iteration:{p.ir_id}:{i.id}"
            kind = ERROR_KIND.get(i.kind, "build")
            nodes[uid] = error_node(uid, secret_free(i.title), kind, secret_free(i.text or i.title)[:MESSAGE_MAX],
                                    artifact_type="iteration", artifact_id=meta.iteration_id,
                                    process_id=p.api_id, iteration=meta.n, input_kind=i.kind,
                                    ref=secret_free(i.ref) if i.ref else None,
                                    path=i.path)
            add_rel("ABOUT", uid, process_uid)
            if i.path:
                add_rel("FAILED_AT", uid, codefile(p.ir_id, i.path))
    if meta.edit_set and meta.processes:
        first = meta.processes[0]
        uid = f"memory:edit-set:{first.ir_id}:{meta.n}"
        lines = [f"{f.change} {f.path}" for f in meta.edit_set]
        nodes[uid] = MemNode(label="Memory", uid=uid, name=f"hand edits of iteration {meta.n}",
                             text=f"Hand edits brought in by iteration {meta.n}: " + "; ".join(lines),
                             props=clean({"kind": "note", "source": "edit set", "created_by": "system",
                                          "artifact_type": "iteration", "artifact_id": meta.iteration_id,
                                          "process_id": first.api_id, "iteration": meta.n}))
        for f in meta.edit_set:
            if f.change != "deleted":
                add_rel("ABOUT", uid, codefile(first.ir_id, f.path))
        add_rel("ABOUT", uid, f"process:{first.ir_id}")
    return [ScopedDelta(scope=f"iteration:{meta.n}", nodes=list(nodes.values()), rels=list(rels.values()))]
