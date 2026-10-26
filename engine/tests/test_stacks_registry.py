"""The stack registry, availability reasons and the .NET wrapper (target stacks, Task 1)."""
from __future__ import annotations

import inspect

import pytest

from engine.dotnet_gen.generator import generate_solution
from engine.dotnet_gen.runner import DotnetRunner
from engine.stacks import all_stacks, get_stack, stack_status
from engine.stacks.base import Mirrors, StackRunner


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


def test_get_stack_names_a_known_stack_that_is_not_implemented():
    with pytest.raises(KeyError, match="stack 'go' is not implemented yet"):
        get_stack("go")


_RUNNER_METHODS = ("restore", "build", "format_write", "format_check", "test", "run_service", "env")


@pytest.mark.parametrize("stack", all_stacks(), ids=lambda s: s.key)
def test_every_runner_has_the_protocol_signatures(stack, tmp_path):
    runner = stack.runner(tmp_path)
    assert isinstance(runner, StackRunner)
    for name in _RUNNER_METHODS:
        want = inspect.signature(getattr(StackRunner, name))
        got = inspect.signature(getattr(type(runner), name))
        assert [(p.name, p.kind, p.default) for p in got.parameters.values()] == \
               [(p.name, p.kind, p.default) for p in want.parameters.values()], name


def test_dotnet_runner_called_positionally_through_the_protocol(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(DotnetRunner, "_run", lambda self, args, timeout_s=None, env=None:
                        calls.append(("run", args, env)) or (0, ""))
    monkeypatch.setattr(DotnetRunner, "run_service", lambda self, project, port, env=None, timeout_s=180.0,
                        health_path="/health": calls.append(("service", project, port, env, timeout_s)) or "h")
    (tmp_path / "GmfCredit.sln").write_text("")
    api = tmp_path / "src" / "GmfCredit.Api"
    api.mkdir(parents=True)
    (api / "GmfCredit.Api.csproj").write_text("<Project/>")
    (tmp_path / "src" / "GmfCredit.Api" / "bin").mkdir()
    (tmp_path / "src" / "GmfCredit.Api" / "bin" / "Copy.Api.csproj").write_text("")  # build output: ignored

    runner = get_stack("dotnet").runner(tmp_path)
    assert runner.restore({"A": "1"}).ok
    assert calls[-1] == ("run", ["restore", str((tmp_path / "GmfCredit.sln").resolve())], {"A": "1"})
    assert runner.run_service(5123, {"B": "2"}, 30.0) == "h"
    assert calls[-1] == ("service", "src/GmfCredit.Api/GmfCredit.Api.csproj", 5123, {"B": "2"}, 30.0)

    given = get_stack("dotnet").runner(tmp_path, api_entry="src/X.Api/X.Api.csproj")
    given.run_service(5124, None, 10.0)
    assert calls[-1][1] == "src/X.Api/X.Api.csproj"


def test_dotnet_runner_refuses_an_ambiguous_api_project(tmp_path):
    for app in ("A", "B"):
        (tmp_path / f"{app}.Api").mkdir()
        (tmp_path / f"{app}.Api" / f"{app}.Api.csproj").write_text("")
    with pytest.raises(FileNotFoundError, match="expected one"):
        get_stack("dotnet").runner(tmp_path).api_project()
