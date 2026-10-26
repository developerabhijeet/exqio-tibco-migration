"""Edited build upload (sub-project 6, Task 7): the zip is checked entry by entry, compared with the
baseline build's own folder (CRLF-normalised), previewed, and resumed at build, design or infra.

The zip is a security boundary: traversal, absolute paths, symbolic links, oversize and
high-ratio entries, too many entries, and duplicate or case-colliding names are refused before an
edit set or an iteration exists, and nothing stays on disk."""
from __future__ import annotations

import io
import json
import os
import stat
import warnings
import zipfile
from collections.abc import Callable

import pytest
from sqlalchemy import func, select

from api.app.models import Build, EditSet, Iteration, Process
from api.app.services import edits as edits_svc
from api.app.services.edits import FileChange, decide_resume, diff_tree, text_sha256
from api.tests import test_build_verify as build_tests

scenario_db = build_tests.scenario_db

DFR = "dealerfunding.DealerFundingRequest"
#: a production file the Code Builder fills, and a test file the Test Engineer wrote (DealerFundingRequest)
AGENT_FILE = "src/GmfCredit.Core/Rules/DealerFundingRequestConditions.cs"
TEST_FILE = "tests/GmfCredit.Tests/DealerFundingRequestRulesTests.cs"
ENGINEER = {"X-User": "engineer.ravi"}
QA = {"X-User": "qa.priya"}


# ------------------------------------------------------------------ fixtures and helpers
@pytest.fixture
def engineer() -> dict[str, str]:
    return ENGINEER


@pytest.fixture
def qa() -> dict[str, str]:
    return QA


@pytest.fixture
def tmp_data_dir(settings):
    return settings.data_dir.resolve()


@pytest.fixture
async def finished_build(client, monkeypatch) -> str:
    _, _, build_id, _ = await build_tests._built(client, monkeypatch, DFR)
    return build_id


@pytest.fixture
async def other_project(client) -> str:
    return (await client.post("/projects", json={"name": "other"})).json()["id"]


async def download(client, build_id: str) -> bytes:
    r = await client.get(f"/builds/{build_id}/download")
    assert r.status_code == 200, r.text
    return r.content


def rezip(data: bytes, change: Callable[[str, bytes], bytes | None], prefix: str = "") -> bytes:
    """Every entry of ``data`` through ``change`` (``None`` drops it), under ``prefix``."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            new = change(info.filename, src.read(info))
            if new is not None:
                dst.writestr(prefix + info.filename, new)
    return out.getvalue()


def edit(data: bytes, edits: dict[str, Callable[[str], str]]) -> bytes:
    return rezip(data, lambda n, b: edits[n](b.decode()).encode() if n in edits else b)


def add_files(data: bytes, files: dict[str, bytes]) -> bytes:
    out = io.BytesIO(data)
    with zipfile.ZipFile(out, "a") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return out.getvalue()


def crlf_everything(data: bytes) -> bytes:
    def to_crlf(name: str, b: bytes) -> bytes:
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            return b
        return b if "\x00" in text else text.replace("\r\n", "\n").replace("\n", "\r\n").encode()

    return rezip(data, to_crlf)


def replace_body(text: str) -> str:
    return text + "\n// hand edit: the engineer's body\n"


def replace_test(text: str) -> str:
    return text + "\n// hand edit: the engineer's test\n"


def zip_with(name: str, data: bytes, *, mode: int | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        info = zipfile.ZipInfo(name)
        if mode is not None:
            info.external_attr = mode << 16
        zf.writestr(info, data)
    return out.getvalue()


def zip_of(entries: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # a duplicate name is the point of one test
        for name, data in entries:
            zf.writestr(name, data)
    return out.getvalue()


async def post_edits(client, build_id: str, data: bytes, headers=ENGINEER):
    return await client.post(f"/builds/{build_id}/edits", files={"file": ("b.zip", data)}, headers=headers)


async def counts(client) -> tuple[int, int]:
    app = client._transport.app
    async with app.state.sessionmaker() as s:
        return (await s.scalar(select(func.count()).select_from(EditSet)),
                await s.scalar(select(func.count()).select_from(Iteration).where(Iteration.kind == "edits")))


# ------------------------------------------------------------------ the brief's tests
async def test_two_edits_are_exactly_what_is_listed(client, finished_build, engineer):          # spec criterion 2
    z = await download(client, finished_build)
    z = edit(z, {AGENT_FILE: replace_body, TEST_FILE: replace_test})
    r = await client.post(f"/builds/{finished_build}/edits", files={"file": ("b.zip", z)}, headers=engineer)
    assert r.status_code == 201, r.text
    files = r.json()["files"]
    assert [(f["path"], f["change"]) for f in files] == sorted([(AGENT_FILE, "modified"), (TEST_FILE, "modified")])
    assert r.json()["resume_phase"] == "build"


async def test_crlf_resave_is_not_an_edit(client, finished_build, engineer):                      # Review Focus 1
    z = crlf_everything(await download(client, finished_build))
    r = await client.post(f"/builds/{finished_build}/edits", files={"file": ("b.zip", z)}, headers=engineer)
    assert r.status_code == 422 and r.json()["detail"] == "no changes in the upload"  # nothing changed: refused before any iteration


async def test_infra_only_edits_resume_at_infra(client, finished_build, engineer):
    z = edit(await download(client, finished_build), {"Dockerfile": lambda t: t + "\n# note\n"})
    assert (await client.post(f"/builds/{finished_build}/edits", files={"file": ("b.zip", z)}, headers=engineer)).json()["resume_phase"] == "infra"


async def test_baseline_of_another_project_is_refused(client, finished_build, other_project, engineer):
    z = await download(client, finished_build)
    r = await client.post(f"/projects/{other_project}/edits", files={"file": ("b.zip", z)}, headers=engineer)
    assert r.status_code == 422 and r.json()["detail"] == "the baseline names a build of another project"


async def test_unsafe_entry_is_refused_before_anything_is_written(client, finished_build, engineer, tmp_data_dir):
    r = await client.post(f"/builds/{finished_build}/edits", files={"file": ("b.zip", zip_with("../evil", b"x"))}, headers=engineer)
    assert r.status_code == 422 and "unsafe path" in r.json()["detail"] and not list((tmp_data_dir / "edits").glob("*"))


async def test_qa_cannot_upload_edits(client, finished_build, qa):
    assert (await client.post(f"/builds/{finished_build}/edits", files={"file": ("b.zip", b"")}, headers=qa)).status_code == 403


# ------------------------------------------------------------------ the archive boundary
@pytest.mark.parametrize(("archive", "detail"), [
    (zip_with("/etc/passwd", b"x"), "unsafe path: /etc/passwd"),
    (zip_with("C:/evil.cs", b"x"), "unsafe path: C:/evil.cs"),
    (zip_with("src\\..\\..\\evil.cs", b"x"), "unsafe path"),
    (zip_with("link", b"/etc/passwd", mode=stat.S_IFLNK | 0o777), "unsafe path: link"),
    (zip_of([("src/A.cs", b"1"), ("src/A.cs", b"2")]), "src/A.cs appears more than once in the archive"),
    (zip_of([("src/A.cs", b"1"), ("SRC/a.cs", b"2")]), "SRC/a.cs and src/A.cs differ only in case or Unicode normalisation"),
    (zip_of([("big.txt", b"\0" * (3 * 1024 * 1024))]), "big.txt expands more than 200 times"),
    (b"not a zip", "not a zip file"),
])
async def test_unsafe_archives_are_refused_and_nothing_is_kept(client, finished_build, tmp_data_dir, archive, detail):
    r = await post_edits(client, finished_build, archive)
    assert r.status_code == 422, r.text
    assert detail in r.json()["detail"]
    assert not list((tmp_data_dir / "edits").glob("*")) and await counts(client) == (0, 0)


async def test_too_many_entries_are_refused(client, finished_build, tmp_data_dir, monkeypatch):
    monkeypatch.setattr(edits_svc, "MAX_ENTRIES", 5)
    r = await post_edits(client, finished_build, zip_of([(f"f{i}.txt", b"x") for i in range(6)]))
    assert r.status_code == 422 and r.json()["detail"] == "the archive has more than 5 entries"
    assert not list((tmp_data_dir / "edits").glob("*"))


async def test_archive_over_the_size_limit_is_refused(client, finished_build, settings, tmp_data_dir):
    settings.upload_max_bytes = 1000
    r = await post_edits(client, finished_build, zip_of([("a.txt", bytes(range(256)) * 8)]))
    assert r.status_code == 413, r.text
    assert not list((tmp_data_dir / "edits").glob("*")) and await counts(client) == (0, 0)


async def test_lying_sizes_are_caught_while_extracting(client, finished_build, settings, tmp_data_dir, monkeypatch):
    """An entry that declares less than it holds is stopped by the byte budget, and the partial tree
    is removed."""
    settings.upload_max_bytes = 4096
    monkeypatch.setattr(edits_svc, "_declared_size", lambda infos: 0)
    r = await post_edits(client, finished_build, zip_of([("a.txt", bytes(range(256)) * 64)]))
    assert r.status_code == 413, r.text
    assert not list((tmp_data_dir / "edits").glob("*"))


async def test_platform_and_repository_folders_are_never_edits(client, finished_build):
    """``.git``, ``.exiqo``, ``bin``/``obj`` and a stale copy of the manifest are not edits of the build."""
    z = add_files(await download(client, finished_build), {
        ".git/config": b"[core]\n", "src/.git/hooks/post-checkout": b"#!/bin/sh\n", ".exiqo/conflicts/x.merge": b"x",
        "src/GmfCredit.Api/bin/Debug/x.dll": b"\0", "src/GmfCredit.Api/obj/x.json": b"{}"})
    r = await post_edits(client, finished_build, z)
    assert r.status_code == 422 and r.json()["detail"] == "no changes in the upload"


async def test_a_wrapping_top_folder_is_stripped(client, finished_build):
    z = rezip(edit(await download(client, finished_build), {AGENT_FILE: replace_body}), lambda n, b: b, "MyBuild/")
    r = await post_edits(client, finished_build, z)
    assert [f["path"] for f in r.json()["files"]] == [AGENT_FILE]


async def test_added_and_deleted_files_carry_their_origin(client, finished_build):
    z = rezip(await download(client, finished_build), lambda n, b: None if n == TEST_FILE else b)
    z = add_files(z, {"src/GmfCredit.Core/Rules/Extra.cs": b"namespace X;\n"})
    files = (await post_edits(client, finished_build, z)).json()["files"]
    assert files == [
        {"path": "src/GmfCredit.Core/Rules/Extra.cs", "change": "added", "origin": "new",
         "sha256": text_sha256(b"namespace X;\n")},
        {"path": TEST_FILE, "change": "deleted", "origin": "ai", "sha256": None}]


async def test_a_forged_manifest_does_not_change_the_baseline(client, finished_build):
    """The server's build folder is the baseline: a manifest claiming every file changed is ignored."""
    def forge(name: str, b: bytes) -> bytes:
        if name != "EXIQO_BASELINE.json":
            return b
        m = json.loads(b)
        return json.dumps({**m, "files": {k: "0" * 64 for k in m["files"]}}).encode()

    r = await post_edits(client, finished_build, rezip(await download(client, finished_build), forge))
    assert r.status_code == 422 and r.json()["detail"] == "no changes in the upload"


# ------------------------------------------------------------------ project route, preview, diff, resume
async def test_project_route_reads_the_baseline_from_the_zip(client, finished_build, engineer):
    build = (await client.get(f"/builds/{finished_build}")).json()
    async with client._transport.app.state.sessionmaker() as s:
        pid = (await s.get(Process, build["process_id"])).project_id
    z = edit(await download(client, finished_build), {AGENT_FILE: replace_body})
    r = await client.post(f"/projects/{pid}/edits", files={"file": ("b.zip", z)}, headers=engineer)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["baseline_build_id"] == finished_build and body["counts"] == {"modified": 1, "added": 0, "deleted": 0}
    iteration = (await client.get(f"/iterations/{body['iteration_id']}")).json()
    assert (iteration["kind"], iteration["n"], iteration["process_ids"], iteration["resume_phase"]) == \
        ("edits", body["iteration"], [build["process_id"]], "build")
    assert iteration["baseline_build_id"] == finished_build


async def test_project_route_without_a_manifest_is_refused(client, finished_build, other_project, engineer):
    z = rezip(await download(client, finished_build), lambda n, b: None if n == "EXIQO_BASELINE.json" else b)
    r = await client.post(f"/projects/{other_project}/edits", files={"file": ("b.zip", z)}, headers=engineer)
    assert r.status_code == 422
    assert r.json()["detail"] == "no EXIQO_BASELINE.json: upload the zip to /builds/{id}/edits instead"


async def test_preview_and_diff(client, finished_build, settings):
    z = edit(await download(client, finished_build), {AGENT_FILE: replace_body})
    es = (await post_edits(client, finished_build, z)).json()
    assert (await client.get(f"/edit-sets/{es['id']}")).json() == es
    d = (await client.get(f"/edit-sets/{es['id']}/diff/{AGENT_FILE}")).json()
    assert d["edited"] == replace_body(d["baseline"]) and d["baseline"]
    assert (await client.get(f"/edit-sets/{es['id']}/diff/GmfCredit.sln")).status_code == 404  # not an edit
    assert (await client.get(f"/edit-sets/{es['id']}/diff/../../etc/passwd")).status_code == 404


async def test_diff_over_a_megabyte_is_413(client, finished_build):
    z = edit(await download(client, finished_build), {AGENT_FILE: lambda t: t + "//" + os.urandom(600 * 1024).hex()})
    es = (await post_edits(client, finished_build, z)).json()
    assert (await client.get(f"/edit-sets/{es['id']}/diff/{AGENT_FILE}")).status_code == 413


async def test_resume_queues_a_build_with_the_edit_set_once(client, finished_build, engineer, qa):
    es = (await post_edits(client, finished_build, edit(await download(client, finished_build),
                                                         {AGENT_FILE: replace_body}))).json()
    assert (await client.post(f"/edit-sets/{es['id']}/resume", headers=qa)).status_code == 403
    r = await client.post(f"/edit-sets/{es['id']}/resume", headers=engineer)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["kind"] == "build" and job["build_id"]
    app = client._transport.app
    async with app.state.sessionmaker() as s:
        build = await s.get(Build, job["build_id"])
        assert (build.edit_set_id, build.iteration) == (es["id"], es["iteration"])
    done = await build_tests._wait_job(client, job["job_id"], timeout=600)
    assert done["status"] == "done", done
    again = await client.post(f"/edit-sets/{es['id']}/resume", headers=engineer)
    assert again.status_code == 409 and again.json()["detail"] == "the edit set was already resumed"


async def test_a_redesign_since_the_baseline_resumes_at_design(client, app, finished_build, engineer):
    async with app.state.sessionmaker() as s:
        build = await s.get(Build, finished_build)
        build.design_hash = "f" * 64  # the build was made from a design that is no longer the approved one
        await s.commit()
    es = (await post_edits(client, finished_build, edit(await download(client, finished_build),
                                                         {AGENT_FILE: replace_body}))).json()
    assert es["resume_phase"] == "design"
    r = await client.post(f"/edit-sets/{es['id']}/resume", headers=engineer)
    assert r.status_code == 202 and r.json()["kind"] == "design"
    gates = await build_tests._gates(client, (await client.get(f"/builds/{finished_build}")).json()["process_id"])
    assert gates["G3"]["state"] == "pending"


# ------------------------------------------------------------------ pure functions
def test_decide_resume():
    mod = [FileChange(path="src/A.cs", change="modified", origin="agent", sha256="1")]
    infra = [FileChange(path=p, change="modified", origin="infra", sha256="1")
             for p in ("infra/live/x.hcl", "Dockerfile", ".dockerignore", "docker-compose.override.yml",
                       "azure-pipelines.yml", ".github/workflows/ci.yml")]
    assert decide_resume(mod, "d", "d") == "build"
    assert decide_resume(infra, "d", "d") == "infra"
    assert decide_resume(infra + mod, "d", "d") == "build"
    assert decide_resume(infra, "d", "e") == "design"
    assert decide_resume(mod, "d", None) == "design"
    assert decide_resume([FileChange(path="infrastructure.md", change="added", origin="new", sha256="1")],
                         "d", "d") == "build"


def test_diff_tree_sorted_and_skips_excluded(tmp_path):
    (tmp_path / "b.cs").write_text("B\r\n", encoding="utf-8")
    (tmp_path / "a.cs").write_text("changed\n", encoding="utf-8")
    (tmp_path / "obj").mkdir()
    (tmp_path / "obj" / "x").write_text("x", encoding="utf-8")
    (tmp_path / "EXPORTED_FROM.md").write_text("note", encoding="utf-8")
    baseline = {"a.cs": text_sha256(b"a\n"), "b.cs": text_sha256(b"B\n"), "c.cs": text_sha256(b"c\n")}
    out = diff_tree(tmp_path, baseline, {"a.cs": "code", "c.cs": "tests"}, frozenset({"obj"}))
    assert [(c.path, c.change, c.origin) for c in out] == [("a.cs", "modified", "code"), ("c.cs", "deleted", "tests")]


# ------------------------------------------------------------------ review fix round 1
VIEWER = {"X-User": "viewer.val"}
SME = {"X-User": "sme.vicky"}


@pytest.mark.parametrize("who", [VIEWER, SME, QA])
async def test_only_engineers_upload_and_resume(client, finished_build, who):
    assert (await post_edits(client, finished_build, b"", headers=who)).status_code == 403
    es = (await post_edits(client, finished_build, edit(await download(client, finished_build),
                                                         {AGENT_FILE: replace_body}))).json()
    r = await client.post(f"/edit-sets/{es['id']}/resume", headers=who)
    assert r.status_code == 403
    assert r.json()["detail"] == ("viewer is read only" if who is VIEWER else "this needs one of: engineer, admin")


async def test_upload_is_audited(client, app, finished_build):                                   # I7
    from api.app.models import AuditLog

    es = (await post_edits(client, finished_build, edit(await download(client, finished_build),
                                                         {AGENT_FILE: replace_body}))).json()
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(AuditLog).where(AuditLog.action == "edit set uploaded"))).all()
    assert len(rows) == 1 and rows[0].actor == "engineer.ravi" and rows[0].process_id
    assert rows[0].detail == {"edit_set_id": es["id"], "baseline_build_id": finished_build, "iteration": es["iteration"],
                              "counts": {"modified": 1, "added": 0, "deleted": 0}}


async def test_a_zip_of_one_subfolder_is_not_shifted(client, finished_build):
    """Only a top folder holding the build (its manifest or solution) is stripped: a zip of ``src/``
    alone keeps its paths, so nothing reads as deleted-and-added under another name."""
    z = rezip(await download(client, finished_build), lambda n, b: b if n.startswith("src/") else None)
    files = (await post_edits(client, finished_build, z)).json()["files"]
    assert files and all(f["change"] == "deleted" and not f["path"].startswith("src/") for f in files)


async def test_a_manifest_naming_another_build_is_refused_on_the_build_route(client, app, finished_build):
    def other(name: str, b: bytes) -> bytes:
        return json.dumps({**json.loads(b), "build_id": "b" * 32}).encode() if name == "EXIQO_BASELINE.json" else b

    r = await post_edits(client, finished_build, rezip(await download(client, finished_build), other))
    assert r.status_code == 422
    assert r.json()["detail"] == f"the zip's EXIQO_BASELINE.json names build {'b' * 32}, not {finished_build}"


async def test_an_upload_without_changes_is_refused_and_leaves_the_active_iteration(client, finished_build):  # final I1
    proc = (await client.get(f"/builds/{finished_build}")).json()["process_id"]
    opened = await client.post(f"/processes/{proc}/iterations", json={"requirements": "a second approver"},
                               headers={"X-User": "architect.lee"})
    assert opened.status_code == 201, opened.text
    r = await post_edits(client, finished_build, crlf_everything(await download(client, finished_build)))
    assert r.status_code == 422 and r.json()["detail"] == "no changes in the upload"
    it = (await client.get(f"/iterations/{opened.json()['id']}")).json()
    assert it["status"] not in ("abandoned", "closed")
    project = (await client.get(f"/iterations/{opened.json()['id']}")).json()["project_id"]
    assert [i["n"] for i in (await client.get(f"/projects/{project}/iterations")).json()] == [1, 2]


def test_os_clutter_is_never_an_edit(tmp_path):
    for name in (".DS_Store", "src/Thumbs.db", "src/desktop.ini", "__MACOSX/x"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert diff_tree(tmp_path, {}, {}, frozenset()) == []
