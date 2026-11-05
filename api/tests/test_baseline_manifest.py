"""The baseline manifest in a build's download (sub-project 6, Task 6): ``EXIQO_BASELINE.json`` names
the build and hashes every zipped file, line endings normalised for text, so an edited zip can be
compared with the build it came from."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from api.app.models import Build
from api.app.models import Process as ProcessRow
from api.app.services.edits import BASELINE_NAME, NOTE_NAME, baseline_manifest, text_sha256
from api.tests import test_build_verify as build_tests

scenario_db = build_tests.scenario_db


@pytest.fixture
async def finished_build(client, monkeypatch) -> str:
    _, _, build_id, _ = await build_tests._built(client, monkeypatch)
    return build_id


async def test_download_carries_baseline_with_every_zipped_file(client, finished_build):
    z = zipfile.ZipFile(io.BytesIO((await client.get(f"/builds/{finished_build}/download")).content))
    m = json.loads(z.read("EXIQO_BASELINE.json"))
    names = {n for n in z.namelist() if n not in ("EXIQO_BASELINE.json", "EXPORTED_FROM.md")}
    assert m["format"] == 1 and m["build_id"] == finished_build and set(m["files"]) == names
    assert all(m["files"][n] == text_sha256(z.read(n)) for n in names)


async def test_manifest_names_the_build_it_came_from(client, finished_build):
    build = (await client.get(f"/builds/{finished_build}")).json()
    z = zipfile.ZipFile(io.BytesIO((await client.get(f"/builds/{finished_build}/download")).content))
    m = json.loads(z.read(BASELINE_NAME))
    proc = next(p for p in (await client.get(f"/projects/{m['project_id']}/processes")).json()
                if p["id"] == build["process_id"])
    assert m["process_id"] == proc["id"] and m["stack"] == "dotnet"
    assert (m["build_hash"], m["design_hash"], m["iteration"]) == (build["build_hash"], build["design_hash"], 1)
    assert NOTE_NAME in z.namelist()


def test_text_sha256_ignores_line_endings_only_for_text():
    assert text_sha256(b"a\r\nb\r\n") == text_sha256(b"a\nb\n")
    assert text_sha256(b"\x00\r\n") != text_sha256(b"\x00\n")
    assert text_sha256(b"\xff\xfe\r\n") != text_sha256(b"\xff\xfe\n")  # not UTF-8: raw bytes


def test_manifest_shape():
    build = Build(id="b1", process_id="p1", status="passed", build_hash="h", design_hash="d", iteration=3)
    proc = ProcessRow(id="p1", project_id="proj", name="X", ir_id="ir", source_path="x.bwp")
    m = baseline_manifest(build, proc, {"a.cs": "1"})
    assert m == {"format": 1, "project_id": "proj", "process_id": "p1", "build_id": "b1", "stack": "dotnet",
                 "build_hash": "h", "design_hash": "d", "iteration": 3, "files": {"a.cs": "1"}}


def test_zip_without_a_baseline_is_unchanged(tmp_path):
    from api.app.routers.build import _zip_solution

    (tmp_path / "A.sln").write_text("x\r\n", encoding="utf-8")
    with zipfile.ZipFile(io.BytesIO(_zip_solution(tmp_path, "note"))) as zf:
        assert sorted(zf.namelist()) == ["A.sln", NOTE_NAME]
