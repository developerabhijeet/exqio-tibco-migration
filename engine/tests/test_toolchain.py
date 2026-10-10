import toolchain

EXPECTED = {"dotnet", "terraform", "terragrunt", "docker", "az", "node"}


def test_detect_returns_all_keys():
    result = toolchain.detect()
    assert set(result) == EXPECTED
    for info in result.values():
        assert isinstance(info["present"], bool)
        assert "version" in info and "path" in info


def test_absent_tool_has_no_path_or_version(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(toolchain.shutil, "which", lambda *a, **k: None)
    result = toolchain.detect()
    assert set(result) == EXPECTED
    for info in result.values():
        assert info == {"present": False, "version": None, "path": None}


def test_local_tools_dir_is_searched_before_path(tmp_path, monkeypatch):
    fake = tmp_path / ".tools" / "bin"
    fake.mkdir(parents=True)
    exe = fake / "terragrunt"
    exe.write_text("#!/bin/sh\necho 'terragrunt version v9.9.9'\n")
    exe.chmod(0o755)
    monkeypatch.setattr(toolchain, "REPO_ROOT", tmp_path)
    info = toolchain.detect()["terragrunt"]
    assert info["present"] is True
    assert info["path"] == str(exe)
    assert "9.9.9" in info["version"]
