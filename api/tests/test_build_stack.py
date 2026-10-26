"""The build pipeline follows the target stack (target-stacks Task 6): the design's stack against the
project's, ``Build.stack``, the worker's toolchain check, one cached toolchain detector, the export
note and the download exclusions, and the memory projection's stack."""
from __future__ import annotations

from typing import ClassVar

import pytest
from sqlalchemy import func, select

from api.app.models import Build, DesignRecord, ModelCall, Project
from api.app.models import Process as ProcessRow
from api.app.routers import stacks as stacks_mod
from api.app.routers.build import DOWNLOAD_EXCLUDED, _export_note, _zip_solution, download_excluded
from api.app.services import build as build_svc
from api.app.services import memory_ingest
from api.app.services import settings as settings_svc
from api.tests.test_build_verify import ARCHITECT, _built, _designed, _wait_job
from engine import stacks
from engine.dotnet_gen.generator import GenFile
from engine.tests.stack_helpers import unimplement
from engine.tests.test_stacks_agents import JAVA_TESTS, TESTS, JavaLikeStack, _index


@pytest.fixture
def approved_g3_dotnet(client):
    async def make():
        pid, proc, _ = await _designed(client)
        return pid, proc
    return make


async def test_build_refused_when_design_stack_differs(client, approved_g3_dotnet, monkeypatch):
    """Review Focus 4: the stack switched after G3 approval answers 409 and creates no build row."""
    pid, proc = await approved_g3_dotnet()
    real = stacks_mod.stack_status
    monkeypatch.setattr(stacks_mod, "stack_status",
                        lambda key, tc, en: real("dotnet", tc, en).model_copy(update={"key": key}))
    r = await client.patch(f"/projects/{pid}", json={"target_stack": "java"}, headers=ARCHITECT)
    assert r.status_code == 200, r.text
    r = await client.post(f"/processes/{proc}/build", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == "the approved design is for dotnet, the project now targets java: re-run Design"
    assert (await client.get(f"/processes/{proc}/builds")).json() == []


async def test_build_refused_for_a_stack_this_install_cannot_build(client, app, approved_g3_dotnet, monkeypatch):
    unimplement(monkeypatch, "python")
    pid, proc = await approved_g3_dotnet()
    async with app.state.sessionmaker() as s:
        project = await s.get(Project, pid)
        project.target_stack = "python"
        for row in (await s.execute(select(DesignRecord).where(DesignRecord.process_id == proc))).scalars():
            row.record = {**row.record, "stack": "python"}
        await s.commit()
    r = await client.post(f"/processes/{proc}/build", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == ("the python stack cannot be built on this install: "
                                  "stack 'python' is not implemented yet")
    assert (await client.get(f"/processes/{proc}/builds")).json() == []


async def test_dotnet_build_records_stack(client, app, monkeypatch):
    _, proc_id, build_id, build = await _built(client, monkeypatch)
    assert build["stack"] == "dotnet"
    assert (await client.get(f"/processes/{proc_id}/design")).json()["stack"] == "dotnet"  # the badge's source
    assert [b["stack"] for b in (await client.get(f"/processes/{proc_id}/builds")).json()] == ["dotnet"]
    restore = build["steps"][0]
    assert restore["ok"] is False and restore["log"] == build_svc.NO_DOTNET  # the .NET message, unchanged
    async with app.state.sessionmaker() as s:
        row = await s.get(Build, build_id)
        assert row.stack == "dotnet"
        built = await memory_ingest.load_build(s, await s.get(ProcessRow, proc_id), build_id)
    assert built is not None and built.stack == "dotnet"  # X6: the projector gets the build's stack


# ---------------------------------------------------------------- worker toolchain check
class _Stub(JavaLikeStack):
    """A registered Java stand-in whose generator must never run in these tests."""
    generated: ClassVar[list[str]] = []

    def generate(self, *a, **k):
        self.generated.append("called")
        raise AssertionError("the generator ran without the toolchain")


@pytest.fixture
def java_registered(monkeypatch):
    _Stub.generated.clear()
    monkeypatch.setitem(stacks._FACTORIES, "java", _Stub)
    monkeypatch.delitem(stacks._INSTANCES, "java", raising=False)
    yield
    stacks._INSTANCES.pop("java", None)


async def _to_java(app, pid: str, proc: str) -> None:
    async with app.state.sessionmaker() as s:
        (await s.get(Project, pid)).target_stack = "java"
        for row in (await s.execute(select(DesignRecord).where(DesignRecord.process_id == proc))).scalars():
            row.record = {**row.record, "stack": "java"}
        await s.commit()


async def test_non_dotnet_build_fails_before_generating_without_its_tools(client, app, settings, monkeypatch,
                                                                          approved_g3_dotnet, java_registered):
    """X14a: the worker checks the stack's tools before stage 2 and fails the job naming them."""
    pid, proc = await approved_g3_dotnet()
    await _to_java(app, pid, proc)
    monkeypatch.setattr(build_svc, "toolchain", lambda: {"java": {"present": False}, "mvn": {"present": False}})
    r = await client.post(f"/processes/{proc}/build", json={})
    assert r.status_code == 202, r.text
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "failed"
    build = (await client.get(f"/builds/{r.json()['build_id']}")).json()
    assert build["status"] == "failed" and build["stack"] == "java"
    assert build["error"] == ("ToolchainMissing: the java toolchain is missing on this worker: java, mvn; "
                              "install it (Settings shows the toolchains) and build again")
    assert _Stub.generated == []
    async with app.state.sessionmaker() as s:
        calls = await s.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.job_id == job["id"]))
    assert calls == 0  # no model was paid to write code nothing could compile


async def test_a_disabled_stack_is_refused_before_a_build_row(client, app, settings, approved_g3_dotnet,
                                                             java_registered):
    pid, proc = await approved_g3_dotnet()
    await _to_java(app, pid, proc)
    app.state.settings.enabled_stacks = "dotnet"
    r = await client.post(f"/processes/{proc}/build", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == ("the java stack cannot be built on this install: "
                                  "not enabled on this install (stacks.enabled)")
    assert (await client.get(f"/processes/{proc}/builds")).json() == []


def test_missing_tools_lists_every_absent_tool(monkeypatch, java_registered):
    monkeypatch.setattr(build_svc, "toolchain", lambda: {"java": {"present": True}, "mvn": {"present": False}})
    assert build_svc.missing_tools(stacks.get_stack("java")) == ["mvn"]
    monkeypatch.setattr(build_svc, "find_dotnet", lambda: None)
    assert build_svc.missing_tools(stacks.get_stack("dotnet")) == ["dotnet"]


def test_find_dotnet_survives_a_failing_detection(monkeypatch):
    def broken():
        raise ImportError("scripts/toolchain.py is not there")

    monkeypatch.setattr("engine.dotnet_gen.runner._toolchain_detect", broken)
    monkeypatch.setattr(settings_svc, "_toolchain_cache", None)
    monkeypatch.setattr(build_svc.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert build_svc.find_dotnet() == "/usr/bin/dotnet"


def test_inventory_reads_has_body_from_the_filled_file(java_registered):
    """The index is written before the Test Engineer runs; a filled body has to count."""
    files = [GenFile(path=TESTS, content=JAVA_TESTS), _index()]
    by_rule = {t["rule_key"]: t["has_body"] for t in build_svc.test_inventory(files, "app", "java") if t["rule_key"]}
    assert by_rule == {"R1": False, "R2": False}
    filled = JAVA_TESTS.replace('fail("no test body for R1");', "assertThat(count(1)).isEqualTo(2);")
    files = [GenFile(path=TESTS, content=filled), _index()]
    by_rule = {t["rule_key"]: t["has_body"] for t in build_svc.test_inventory(files, "app", "java") if t["rule_key"]}
    assert by_rule == {"R1": True, "R2": False}
    branch = next(t for t in build_svc.test_inventory(files, "app", "java") if t["kind"] == "branch")
    assert branch["has_body"] is True


def test_mirrors_come_from_settings(settings):
    settings.nuget_source, settings.maven_mirror_url = "https://nuget.gmf", "https://maven.gmf"
    settings.goproxy, settings.uv_index_url = "https://go.gmf", "https://pypi.gmf/simple"
    m = build_svc.mirrors(settings)
    assert (m.nuget, m.maven, m.goproxy, m.pypi) == ("https://nuget.gmf", "https://maven.gmf",
                                                     "https://go.gmf", "https://pypi.gmf/simple")


def test_one_cached_toolchain_detection(monkeypatch):
    calls = []

    def detect():
        calls.append(1)
        return {"dotnet": {"present": True, "version": "10", "path": "/opt/dotnet"}}

    monkeypatch.setattr("engine.dotnet_gen.runner._toolchain_detect", detect)
    monkeypatch.setattr(settings_svc, "_toolchain_cache", None)
    first = settings_svc.detect_toolchains()
    first["dotnet"]["path"] = "changed by a caller"
    assert settings_svc.tool_path("dotnet") == "/opt/dotnet" and build_svc.toolchain()["dotnet"]["present"]
    assert len(calls) == 1
    settings_svc.detect_toolchains(max_age_s=0)
    assert len(calls) == 2
    monkeypatch.setattr(settings_svc, "_toolchain_cache", None)


# ---------------------------------------------------------------- export
def test_export_note_names_the_target_stack():
    note = _export_note(Build(id="b1", process_id="p1", status="passed", stack="dotnet"), None)
    assert "| Target stack | dotnet (C# · .NET 10 · ASP.NET Core) |" in note
    assert "| Target stack | dotnet" in _export_note(Build(id="b2", process_id="p1", status="failed"), None)


def test_download_exclusions_follow_the_stack():
    assert download_excluded("dotnet") == DOWNLOAD_EXCLUDED == stacks.get_stack("dotnet").download_excluded
    assert download_excluded(None) == DOWNLOAD_EXCLUDED
    assert download_excluded("java") == frozenset({"target/", ".exiqo/logs"})
    assert download_excluded("go") == frozenset({".exiqo-bin", ".exiqo"})
    assert download_excluded("python") == stacks.get_stack("python").download_excluded


def test_java_download_keeps_the_maven_settings_but_not_the_logs(tmp_path):
    """``mvn -s .exiqo/maven-settings.xml`` is how the bundle builds: the zip must carry it."""
    import io
    import zipfile
    for rel in ("pom.xml", ".exiqo/maven-settings.xml", ".exiqo/logs/service-1.log", "target/app.jar",
                "src/main/java/A.java"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    with zipfile.ZipFile(io.BytesIO(_zip_solution(tmp_path, None, download_excluded("java")))) as zf:
        names = set(zf.namelist())
    assert names == {"pom.xml", ".exiqo/maven-settings.xml", "src/main/java/A.java"}


def test_java_download_drops_only_the_root_target_folder(tmp_path):
    """A package or contract folder called ``target`` (a TIBCO module named Target) is source."""
    import io
    import zipfile
    for rel in ("target/app.jar", "src/main/java/com/x/target/Contract.java", "core/target/B.java"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    with zipfile.ZipFile(io.BytesIO(_zip_solution(tmp_path, None, download_excluded("java")))) as zf:
        assert set(zf.namelist()) == {"src/main/java/com/x/target/Contract.java", "core/target/B.java"}


def test_alembic_has_one_head_and_it_is_the_stacks_revision():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0011_target_stack"]  # X25 (down '0010' at integration)
