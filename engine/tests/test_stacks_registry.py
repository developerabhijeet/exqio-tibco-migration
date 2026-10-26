"""The stack registry, availability reasons and the .NET wrapper (target stacks, Task 1)."""
from __future__ import annotations

import pytest

from engine.dotnet_gen.generator import generate_solution
from engine.stacks import get_stack, stack_status
from engine.stacks.base import Mirrors


def test_registry_has_dotnet_and_rejects_unknown():
    assert get_stack("dotnet").key == "dotnet"
    with pytest.raises(KeyError, match="unknown stack 'cobol'"):
        get_stack("cobol")


def test_status_reasons_in_order():
    tools = {"dotnet": {"present": True}, "java": {"present": False}, "mvn": {"present": False}}
    assert stack_status("dotnet", tools, ["dotnet"]).available is True
    assert stack_status("java", tools, ["dotnet"]).reason in (
        "not implemented yet", "not enabled on this install (stacks.enabled)")
    assert stack_status("dotnet", {"dotnet": {"present": False}}, ["dotnet"]).reason == "toolchain missing: dotnet"
    assert stack_status("dotnet", tools, ["java"]).reason == "not enabled on this install (stacks.enabled)"


def test_dotnet_wrapper_is_byte_identical(tmp_path, gmf_app, gmf_inputs):
    a = generate_solution(gmf_app, *gmf_inputs, tmp_path / "a")
    b = get_stack("dotnet").generate(gmf_app, *gmf_inputs, tmp_path / "b", Mirrors())
    assert a.build_hash == b.build_hash and [f.path for f in a.files] == [f.path for f in b.files]
    assert [f.content for f in a.files] == [f.content for f in b.files]


def test_dotnet_wrapper_passes_the_nuget_mirror(tmp_path, gmf_app, gmf_inputs):
    mirror = "https://nuget.gmf.example/v3/index.json"
    a = generate_solution(gmf_app, *gmf_inputs, tmp_path / "a", mirror)
    b = get_stack("dotnet").generate(gmf_app, *gmf_inputs, tmp_path / "b", Mirrors(nuget=mirror))
    assert a.build_hash == b.build_hash


def test_dotnet_language_delegates_to_builder_checks():
    lang = get_stack("dotnet").language
    assert lang.placeholder.search('throw new NotImplementedException("agent fills: X");')
    assert lang.is_test_path("tests/GmfCredit.Tests/A.cs") and not lang.is_test_path("src/A.cs")


def test_dotnet_layout_derives_from_solution_layout():
    from engine.dotnet_gen.generator import solution_layout

    layout = get_stack("dotnet").layout("GmfCredit")
    raw = solution_layout("GmfCredit")
    assert layout.solution_file == "GmfCredit.sln" and layout.contract_file is None
    assert layout.api_entry == raw["api_project"]
    assert layout.projects["tests"].build_file == raw["test_project"]
    assert layout.test_root == raw["projects"]["tests"]["folder"]
