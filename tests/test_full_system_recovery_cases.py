from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from eval.full_system_cases.recovery import RECOVERY_CASES, materialize
from refactorika.harness import mark_escalated, verify_edits


def _case(name: str):  # type annotation omitted to keep the fixture import compact
    return next(case for case in RECOVERY_CASES if case.name == name)


def test_cases_expose_only_generic_prompt_and_keep_oracle_hidden(tmp_path: Path) -> None:
    assert len(RECOVERY_CASES) == 3
    for case in RECOVERY_CASES:
        repo = materialize(case, tmp_path / case.name)
        assert case.initial_prompt == "Refactor this codebase while preserving behavior."
        assert not (repo / "tests" / "oracle").exists()
        assert case.hidden_oracle
        assert case.expected_diagnostics


def test_type_clean_behavior_break_is_rejected_by_visible_tests(tmp_path: Path) -> None:
    case = _case("type_clean_threshold_regression")
    repo = materialize(case, tmp_path / case.name)
    originals = {path: (repo / path).read_text() for path in case.attempts[0]}

    record = verify_edits(repo, case.attempts[0], required_gates=("tests",))

    assert record.status == "rolled-back"
    assert record.checks.parse is True
    assert record.checks.typecheck in (True, None)
    assert record.checks.tests is False
    assert case.expected_gate == "tests"
    assert all((repo / path).read_text() == content for path, content in originals.items())


def test_nullable_return_is_classified_as_targeted_type_repair(tmp_path: Path) -> None:
    if shutil.which("pyright") is None:
        pytest.skip("pyright is not installed in this test environment")
    case = _case("nullable_return_requires_targeted_repair")
    repo = materialize(case, tmp_path / case.name)
    originals = {path: (repo / path).read_text() for path in case.attempts[0]}

    record = verify_edits(repo, case.attempts[0], required_gates=("typecheck",))

    assert record.status == "rolled-back"
    assert record.checks.typecheck is False
    assert record.failure_reason is not None
    assert record.failure_reason.startswith("typecheck:")
    assert case.expected_gate == "typecheck"
    assert "app/catalog.py" in case.expected_diagnostics
    assert all((repo / path).read_text() == content for path, content in originals.items())


def test_repeated_invalid_repairs_end_needs_human_and_never_partially_land(
    tmp_path: Path,
) -> None:
    case = _case("repeated_invalid_repairs_escalate")
    repo = materialize(case, tmp_path / case.name)
    originals = {
        path: (repo / path).read_text()
        for path in {relative for attempt in case.attempts for relative in attempt}
    }
    final_record = None

    for retry, attempt in enumerate(case.attempts):
        final_record = verify_edits(repo, attempt, retries=retry)
        assert final_record.status == "rolled-back"
        assert final_record.checks.parse is False
        assert all((repo / path).read_text() == content for path, content in originals.items())

    assert final_record is not None
    terminal = mark_escalated(final_record)
    assert terminal.status == "skipped-needs-human"
    assert terminal.retries == case.max_retries
    assert case.expected_failure == "retry-exhausted"
