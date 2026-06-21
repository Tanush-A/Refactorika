from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from eval.agents.tools import DeveloperTools


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello():\n    return 'hi'\n")
    (tmp_path / "tests" / "test_app.py").write_text("def test_it():\n    pass\n")
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    return tmp_path


def test_lists_globs_and_reads_with_ranges(repo: Path) -> None:
    tools = DeveloperTools(repo)
    assert tools.list_files().data == ["src/app.py", "tests/test_app.py"]
    assert tools.glob_files("src/*.py").data == ["src/app.py"]
    result = tools.read_file("src/app.py", start_line=2, end_line=2)
    assert result.ok
    assert result.data == "2:     return 'hi'"


def test_read_batch_and_output_are_bounded(repo: Path) -> None:
    tools = DeveloperTools(repo, output_limit=30, max_batch_files=1)
    assert tools.read_files(["src/app.py"]).truncated
    failure = tools.read_files(["src/app.py", "tests/test_app.py"])
    assert not failure.ok
    assert failure.error_class == "ValueError"


def test_paths_cannot_escape_repository(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-agent-tool.txt"
    outside.write_text("secret")
    tools = DeveloperTools(repo)
    result = tools.read_file("../outside-agent-tool.txt")
    assert not result.ok
    assert result.error_class == "ValueError"


def test_symlink_cannot_escape_repository(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-symlink-target.txt"
    outside.write_text("secret")
    (repo / "escape").symlink_to(outside)
    result = DeveloperTools(repo).read_file("escape")
    assert not result.ok
    assert result.error_class == "ValueError"


def test_search_and_find_references(repo: Path) -> None:
    tools = DeveloperTools(repo)
    search = tools.search_code("return 'hi'")
    assert search.ok and "src/app.py:2" in search.data
    references = tools.find_references("hello")
    assert references.ok and "src/app.py:1" in references.data
    assert not tools.find_references("not valid!").ok


def test_submit_patch_uses_standard_shape_and_rejects_tests(repo: Path) -> None:
    tools = DeveloperTools(repo)
    result = tools.submit_patch(
        edits={"src/app.py": "VALUE = 2\n", "src/new.py": "VALUE = 3\n"},
        refactor_kind="extract",
        plan_step="step-1",
    )
    assert result.ok
    assert result.data["changed_paths"] == ["src/app.py", "src/new.py"]
    assert (repo / "src/app.py").read_text() == "VALUE = 2\n"

    rejected = tools.submit_patch(
        edits={"tests/test_app.py": "destroyed = True\n"},
        refactor_kind="test-cheat",
        plan_step="step-2",
    )
    assert not rejected.ok
    assert (repo / "tests/test_app.py").read_text() == "def test_it():\n    pass\n"


def test_patch_validation_is_atomic(repo: Path) -> None:
    original = (repo / "src/app.py").read_text()
    result = DeveloperTools(repo).submit_patch(
        edits={"src/app.py": "changed\n", "tests/test_app.py": "changed\n"},
        refactor_kind="rename",
        plan_step="step-1",
    )
    assert not result.ok
    assert (repo / "src/app.py").read_text() == original


def test_gate_tools_are_structured_and_timeout(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    tools = DeveloperTools(
        repo,
        test_command=("fake-test",),
        lint_command=("fake-lint",),
        typecheck_command=("fake-typecheck",),
        timeout=0.01,
    )

    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=0.01)

    monkeypatch.setattr(subprocess, "run", timeout)
    for result in (tools.run_tests(), tools.run_lint(), tools.run_typecheck()):
        assert not result.ok
        assert result.error_class == "ToolTimeout"
        assert result.seconds >= 0


def test_git_tools_do_not_mutate_repository(repo: Path) -> None:
    tools = DeveloperTools(repo)
    assert tools.git_status().ok
    assert tools.git_diff().ok
