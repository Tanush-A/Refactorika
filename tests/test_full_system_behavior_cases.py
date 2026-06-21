from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from eval.full_system_cases.behavior import BEHAVIOR_CASES, GENERIC_USER_PROMPT, BehaviorCase


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _pytest(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_behavior_case_manifest_is_complete() -> None:
    assert GENERIC_USER_PROMPT == "refactor this codebase"
    assert len(BEHAVIOR_CASES) == 3
    assert len({case.name for case in BEHAVIOR_CASES}) == len(BEHAVIOR_CASES)
    for case in BEHAVIOR_CASES:
        assert case.user_prompt == GENERIC_USER_PROMPT
        assert case.structural_expectations
        assert case.trap_edits
        assert all(path.startswith("tests/oracle/") for path in case.hidden_tests)
        assert not any(path.startswith("tests/oracle/") for path in case.baseline_files)


@pytest.mark.parametrize("case", BEHAVIOR_CASES, ids=lambda case: case.name)
def test_materialized_baseline_and_hidden_oracle_pass(case: BehaviorCase, tmp_path: Path) -> None:
    repo = case.materialize(tmp_path / case.name)
    assert not (repo / "tests" / "oracle").exists()
    assert _pytest(repo).returncode == 0

    _write_files(repo, case.hidden_tests)
    result = _pytest(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", BEHAVIOR_CASES, ids=lambda case: case.name)
def test_hidden_oracle_rejects_calibrated_behavior_trap(case: BehaviorCase, tmp_path: Path) -> None:
    repo = case.materialize(tmp_path / case.name)
    _write_files(repo, case.trap_edits)

    # The plausible bad refactor survives the visible suite, which makes this a
    # useful held-out behavior case instead of a public-test exercise.
    visible = _pytest(repo)
    assert visible.returncode == 0, visible.stdout + visible.stderr

    _write_files(repo, case.hidden_tests)
    oracle = _pytest(repo)
    assert oracle.returncode != 0
