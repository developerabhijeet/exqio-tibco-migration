"""Three-way merge of hand edits into a fresh generation (sub-project 6, Task 8) and the protected
``hand`` paths the agents leave alone."""
from __future__ import annotations

from pathlib import Path

import pytest

from api.app.services.edits import FileChange
from engine.dotnet_gen.generator import GeneratedSolution, GenFile
from engine.dotnet_gen.repair import check_diff
from engine.sourcing.merge import MergeResult, apply_edits, hand_paths, three_way

CSPROJ = "src/App.Core/App.Core.csproj"
HAND = "src/App.Core/Services/XService.cs"
DIFF_ON_HAND_FILE = f"--- a/{HAND}\n+++ b/{HAND}\n@@ -1,2 +1,2 @@\n class X {{\n-  int A;\n+  int B;\n"


def mod(path: str) -> FileChange:
    return FileChange(path=path, change="modified", origin="agent", sha256="0")


def _tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8", newline="")
    return root


@pytest.fixture
def tmp_solution(tmp_path):
    class Make:
        root = tmp_path / "build"

        def __call__(self, ours: str = "x\nGEN\n", extra: dict[str, str] | None = None) -> GeneratedSolution:
            files = {"src/A.cs": ours, CSPROJ: "<Project />\n", **(extra or {})}
            _tree(self.root, files)
            return GeneratedSolution(root=self.root, name="App", build_hash="h",
                                     files=[GenFile(path=p, content=c, origin="agent" if p.endswith(".cs") else "code")
                                            for p, c in files.items()])

    return Make()


@pytest.fixture
def base_root(tmp_path):
    def make(a: str = "x\nold\n") -> Path:
        return _tree(tmp_path / "baseline", {"src/A.cs": a, CSPROJ: "<Project />\n"})

    return make


@pytest.fixture
def edited_root(tmp_path):
    def make(a: str | bytes = "x\nHAND\n", extra: dict[str, str | bytes] | None = None) -> Path:
        return _tree(tmp_path / "edited", {"src/A.cs": a, **(extra or {})})

    return make


@pytest.fixture
def solution_with_hand_file(tmp_path) -> GeneratedSolution:
    text = "class X {\n  int A;\n}\n"
    _tree(tmp_path, {HAND: text})
    return GeneratedSolution(root=tmp_path, name="App", build_hash="h",
                             files=[GenFile(path=HAND, content=text, origin="agent")])


# ------------------------------------------------------------------ the brief's tests
def test_unchanged_generation_keeps_the_edit_verbatim():
    text, conflicted = three_way("a\nb\nc\n", "a\nb\nc\n", "a\nB\nc\n")
    assert (text, conflicted) == ("a\nB\nc\n", False)


def test_disjoint_changes_merge():
    text, conflicted = three_way("a\nb\nc\nd\n", "A\nb\nc\nd\n", "a\nb\nc\nD\n")
    assert (text, conflicted) == ("A\nb\nc\nD\n", False)


def test_conflict_keeps_hand_version_and_writes_merge_file(tmp_solution, base_root, edited_root):  # Review Focus 2
    res = apply_edits(tmp_solution(ours="x\nGEN\n"), base_root("x\nold\n"), edited_root("x\nHAND\n"), [mod("src/A.cs")], set())
    assert res == [MergeResult(path="src/A.cs", outcome="conflict", detail=".exiqo/conflicts/src/A.cs.merge")]
    assert (tmp_solution.root / "src/A.cs").read_text() == "x\nHAND\n"
    merge = (tmp_solution.root / ".exiqo/conflicts/src/A.cs.merge").read_text()
    assert "GEN" in merge and "HAND" in merge and "old" in merge


def test_deleted_required_scaffold_is_restored(tmp_solution, base_root, edited_root):
    res = apply_edits(tmp_solution(), base_root(), edited_root(), [FileChange(path="src/App.Core/App.Core.csproj", change="deleted", origin="code", sha256=None)], {"src/App.Core/App.Core.csproj"})
    assert res[0].outcome == "restored"


def test_repair_refuses_a_diff_on_a_hand_path(solution_with_hand_file):                 # Review Focus 3
    problem = check_diff(DIFF_ON_HAND_FILE, solution_with_hand_file, protected={"src/App.Core/Services/XService.cs"})
    assert problem == ({}, "src/App.Core/Services/XService.cs is a hand edit; the repair may not change it")


# ------------------------------------------------------------------ the rest of the rules
def test_fresh_equal_to_base_keeps_the_edit_and_the_solution_follows(tmp_solution, base_root, edited_root):
    sol = tmp_solution(ours="x\nold\n")
    res = apply_edits(sol, base_root("x\nold\n"), edited_root("x\r\nHAND\r\n"), [mod("src/A.cs")], set())
    assert res == [MergeResult(path="src/A.cs", outcome="kept")]
    assert (sol.root / "src/A.cs").read_bytes() == b"x\r\nHAND\r\n"  # verbatim, line endings included
    assert next(f for f in sol.files if f.path == "src/A.cs").content == "x\r\nHAND\r\n"


def test_disjoint_generated_and_hand_changes_are_merged(tmp_solution, base_root, edited_root):
    sol = tmp_solution(ours="A\nb\nc\nd\n")
    res = apply_edits(sol, base_root("a\nb\nc\nd\n"), edited_root("a\r\nb\r\nc\r\nD\r\n"), [mod("src/A.cs")], set())
    assert res == [MergeResult(path="src/A.cs", outcome="merged")]
    assert (sol.root / "src/A.cs").read_bytes() == b"A\r\nb\r\nc\r\nD\r\n"  # the edit's line endings


def test_missing_baseline_folder_keeps_the_edit(tmp_solution, edited_root, tmp_path):
    sol = tmp_solution()
    res = apply_edits(sol, tmp_path / "gone", edited_root(), [mod("src/A.cs")], set())
    assert res == [MergeResult(path="src/A.cs", outcome="kept", detail="baseline folder missing: edit kept as uploaded")]
    assert apply_edits(sol, None, edited_root(), [mod("src/A.cs")], set())[0].outcome == "kept"
    assert (sol.root / "src/A.cs").read_text() == "x\nHAND\n"


def test_added_and_deleted_files(tmp_solution, base_root, edited_root):
    sol = tmp_solution(extra={"src/Old.cs": "old\n"})
    edited = edited_root(extra={"src/New.cs": "new\n", "assets/logo.png": b"\x89PNG\x00\x01"})
    res = apply_edits(sol, base_root(), edited, [
        FileChange(path="assets/logo.png", change="added", origin="new", sha256="0"),
        FileChange(path="src/New.cs", change="added", origin="new", sha256="0"),
        FileChange(path="src/Old.cs", change="deleted", origin="agent", sha256=None),
        FileChange(path=CSPROJ, change="deleted", origin="code", sha256=None)], set())
    assert [(r.path, r.outcome) for r in res] == [("assets/logo.png", "added"), ("src/New.cs", "added"),
                                                  ("src/Old.cs", "deleted"), (CSPROJ, "deleted")]
    assert (sol.root / "assets/logo.png").read_bytes() == b"\x89PNG\x00\x01"
    assert not (sol.root / "src/Old.cs").exists() and not (sol.root / CSPROJ).exists()
    paths = {f.path for f in sol.files}
    assert "src/New.cs" in paths and "src/Old.cs" not in paths and CSPROJ not in paths
    assert hand_paths(res) == {"assets/logo.png", "src/New.cs"}


def test_a_change_outside_the_tree_is_refused(tmp_solution, base_root, edited_root):
    with pytest.raises(ValueError, match="outside"):
        apply_edits(tmp_solution(), base_root(), edited_root(), [mod("../escape.cs")], set())


def test_git_missing_keeps_the_edit_as_a_conflict(tmp_solution, base_root, edited_root):
    sol = tmp_solution(ours="x\nGEN\n")
    res = apply_edits(sol, base_root(), edited_root(), [mod("src/A.cs")], set(), git="/nonexistent/git")
    assert res[0].outcome == "conflict" and "git" in res[0].detail
    assert (sol.root / "src/A.cs").read_text() == "x\nHAND\n"


def test_hand_paths_are_every_outcome_but_deleted():
    res = [MergeResult(path=p, outcome=o) for p, o in
           (("a", "kept"), ("b", "merged"), ("c", "conflict"), ("d", "added"), ("e", "deleted"), ("f", "restored"))]
    assert hand_paths(res) == {"a", "b", "c", "d", "f"}


def test_three_way_runs_in_the_given_tmp_dir(tmp_path):
    three_way("a\n", "a\n", "b\n", tmp_dir=tmp_path / ".exiqo" / "tmp")
    assert (tmp_path / ".exiqo" / "tmp").is_dir() and not list((tmp_path / ".exiqo" / "tmp").iterdir())


def test_a_model_written_baseline_file_keeps_the_edit(tmp_solution, base_root, edited_root):
    """The fresh generation of an ``ai`` file is its placeholder scaffold: merging would put the
    placeholders back, so the edit (which holds the model's fill) is kept."""
    sol = tmp_solution(ours="x\nPLACEHOLDER\nc\n")
    res = apply_edits(sol, base_root("x\nFILLED\nc\n"), edited_root("x\nFILLED\nc\n// hand\n"),
                      [FileChange(path="src/A.cs", change="modified", origin="ai", sha256="0")], set())
    assert res == [MergeResult(path="src/A.cs", outcome="kept", detail="model-written in the baseline build: edit kept as uploaded")]
    assert (sol.root / "src/A.cs").read_text() == "x\nFILLED\nc\n// hand\n"


def test_a_model_written_file_under_a_changed_scaffold_is_a_conflict(tmp_solution, base_root, edited_root):
    """X23: when the design changed, the regenerated scaffold may differ from the one the model
    filled: the hand edit is kept and flagged, never silently re-scaffolded."""
    sol = tmp_solution(ours="x\nNEW SCAFFOLD\nc\n")
    res = apply_edits(sol, base_root("x\nFILLED\nc\n"), edited_root("x\nFILLED\nc\n// hand\n"),
                      [FileChange(path="src/A.cs", change="modified", origin="ai", sha256="0")], set(),
                      scaffold_changed=True)
    assert res == [MergeResult(path="src/A.cs", outcome="conflict", detail=".exiqo/conflicts/src/A.cs.merge")]
    assert (sol.root / "src/A.cs").read_text() == "x\nFILLED\nc\n// hand\n"
    assert "NEW SCAFFOLD" in (sol.root / ".exiqo/conflicts/src/A.cs.merge").read_text()


# ------------------------------------------------------------------ review fix round 1
def test_a_baseline_hand_file_keeps_the_new_edit(tmp_solution, base_root, edited_root):         # I2
    """A file a person edited in the baseline (an earlier edit build) merges against that file,
    not the placeholder scaffold: the placeholders never come back."""
    sol = tmp_solution(ours="x\nPLACEHOLDER\nc\n")
    res = apply_edits(sol, base_root("x\nOLD HAND\nc\n"), edited_root("x\nOLD HAND\nc\n// again\n"),
                      [FileChange(path="src/A.cs", change="modified", origin="hand", sha256="0")], set())
    assert res[0].outcome == "kept" and "hand-edited in the baseline build" in res[0].detail
    assert (sol.root / "src/A.cs").read_text() == "x\nOLD HAND\nc\n// again\n"


def test_a_repair_touched_agent_file_is_treated_as_model_written(tmp_solution, base_root, edited_root):  # I4
    sol = tmp_solution(ours="x\nPLACEHOLDER\nc\n")
    change = FileChange(path="src/A.cs", change="modified", origin="agent", sha256="0")
    res = apply_edits(sol, base_root("x\nREPAIRED\nc\n"), edited_root("x\nREPAIRED\nc\n// hand\n"), [change], set(),
                      model_written={"src/A.cs"})
    assert res[0].outcome == "kept" and (sol.root / "src/A.cs").read_text().endswith("// hand\n")
    res = apply_edits(tmp_solution(ours="x\nPLACEHOLDER\nc\n"), base_root("x\nREPAIRED\nc\n"),
                      edited_root("x\nREPAIRED\nc\n// hand\n"), [change], set(), scaffold_changed=True,
                      model_written={"src/A.cs"})
    assert res[0].outcome == "conflict"


def test_a_formatted_baseline_still_counts_as_an_unchanged_generation(tmp_solution, base_root, edited_root):  # I3
    """The baseline folder went through dotnet format; the fresh generation did not. Layout alone is
    not a generated change: the edit is kept verbatim."""
    sol = tmp_solution(ours="class A {\n  void M() { int x = 1; }\n}\n")
    formatted = "class A\n{\n    void M()\n    {\n        int x = 1;\n    }\n}\n"
    res = apply_edits(sol, base_root(formatted), edited_root(formatted.replace("1;", "2;")), [mod("src/A.cs")], set())
    assert res == [MergeResult(path="src/A.cs", outcome="kept")]
    assert (sol.root / "src/A.cs").read_text() == formatted.replace("1;", "2;")


def test_an_added_file_over_a_fresh_one_is_a_conflict(tmp_solution, base_root, edited_root):
    sol = tmp_solution(extra={"src/New.cs": "generated\n"})
    res = apply_edits(sol, base_root(), edited_root(extra={"src/New.cs": "hand\n"}),
                      [FileChange(path="src/New.cs", change="added", origin="new", sha256="0")], set())
    assert res == [MergeResult(path="src/New.cs", outcome="conflict", detail=".exiqo/conflicts/src/New.cs.merge")]
    assert (sol.root / "src/New.cs").read_text() == "hand\n"
    assert "generated" in (sol.root / ".exiqo/conflicts/src/New.cs.merge").read_text()


def test_a_clean_merge_keeps_the_edited_line_endings(tmp_solution, base_root, edited_root):
    sol = tmp_solution(ours="A\nb\nc\nd\n")
    apply_edits(sol, base_root("a\nb\nc\nd\n"), edited_root("a\r\nb\r\nc\r\nD\r\n"), [mod("src/A.cs")], set())
    assert (sol.root / "src/A.cs").read_bytes() == b"A\r\nb\r\nc\r\nD\r\n"



def test_review_panel_gets_hand_source_files_only_within_the_cap():                              # I6
    from engine.agents.reviewers import HAND_REVIEW_MAX_BYTES, reviewed_files, skipped_hand_files

    files = [GenFile(path="src/A.cs", content="class A {}\n", origin="hand"),
             GenFile(path="src/Big.cs", content="x" * (HAND_REVIEW_MAX_BYTES + 1), origin="hand"),
             GenFile(path="src/App.Core/App.Core.csproj", content="<Project/>", origin="hand"),
             GenFile(path="assets/logo.png", content="\ufffdPNG", origin="hand"),
             GenFile(path="src/B.cs", content="class B {}\n", origin="agent")]
    sol = GeneratedSolution(root=Path("/x"), name="App", files=files, build_hash="h")
    assert set(reviewed_files(sol)) == {"src/A.cs", "src/B.cs"}
    assert skipped_hand_files(sol) == ["src/Big.cs"]
