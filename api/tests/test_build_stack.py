"""The build pipeline follows the target stack (target-stacks Task 6): the design's stack against the
project's, ``Build.stack``, the worker's toolchain check, one cached toolchain detector, the export
note and the download exclusions, and the memory projection's stack."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from api.app.models import Build, DesignRecord, Project
from api.app.models import Process as ProcessRow
from api.app.routers import stacks as stacks_mod
from api.app.routers.build import DOWNLOAD_EXCLUDED, _export_note, download_excluded
from api.app.services import build as build_svc
from api.app.services import memory_ingest
from api.app.services import settings as settings_svc
from api.tests.test_build_verify import ARCHITECT, _built, _designed
from engine import stacks


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


async def test_build_refused_for_a_stack_this_install_cannot_build(client, app, approved_g3_dotnet):
    pid, proc = await approved_g3_dotnet()
    async with app.state.sessionmaker() as s:
        project = await s.get(Project, pid)
        project.target_stack = "java"
        for row in (await s.execute(select(DesignRecord).where(DesignRecord.process_id == proc))).scalars():
            row.record = {**row.record, "stack": "java"}
        await s.commit()
    r = await client.post(f"/processes/{proc}/build", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == ("the java stack cannot be built on this install: "
                                  "stack 'java' is not implemented yet")
    assert (await client.get(f"/processes/{proc}/builds")).json() == []


async def test_dotnet_build_records_stack(client, app, monkeypatch):
    _, proc_id, build_id, build = await _built(client, monkeypatch)
    assert build["stack"] == "dotnet"
    assert [b["stack"] for b in (await client.get(f"/processes/{proc_id}/builds")).json()] == ["dotnet"]
    restore = build["steps"][0]
    assert restore["ok"] is False and restore["log"] == build_svc.NO_DOTNET  # the .NET message, unchanged
    async with app.state.sessionmaker() as s:
        row = await s.get(Build, build_id)
        assert row.stack == "dotnet"
        built = await memory_ingest.load_build(s, await s.get(ProcessRow, proc_id), build_id)
    assert built is not None and built.stack == "dotnet"  # X6: the projector gets the build's stack


# ---------------------------------------------------------------- worker toolchain check
class _Stub:
    key = "java"
    label = "Java (stand-in)"
    required_tools = ("java", "mvn")


def test_worker_names_the_missing_tool(monkeypatch, settings):
    monkeypatch.setitem(stacks._FACTORIES, "java", _Stub)
    monkeypatch.delitem(stacks._INSTANCES, "java", raising=False)
    monkeypatch.setattr(build_svc, "toolchain", lambda: {"java": {"present": True}, "mvn": {"present": False}})
    settings.enabled_stacks = "dotnet,java"
    why = build_svc.stack_tool_missing(stacks.get_stack("java"), settings)
    stacks._INSTANCES.pop("java", None)
    assert why == "toolchain missing: mvn: install it on the worker (Settings shows the toolchains)"


def test_dotnet_keeps_its_own_toolchain_lookup(monkeypatch, settings):
    monkeypatch.setattr(build_svc, "find_dotnet", lambda: None)
    assert build_svc.stack_tool_missing(stacks.get_stack("dotnet"), settings) == build_svc.NO_DOTNET
    monkeypatch.setattr(build_svc, "find_dotnet", lambda: "/usr/bin/dotnet")
    assert build_svc.stack_tool_missing(stacks.get_stack("dotnet"), settings) is None


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
    assert download_excluded("java") == DOWNLOAD_EXCLUDED  # not implemented yet: the default list
