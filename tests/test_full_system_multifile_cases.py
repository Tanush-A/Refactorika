from pathlib import Path

import pytest
from eval.full_system_cases.multifile import CASES, USER_PROMPT, materialize, structural_failures


def test_cases_use_only_the_generic_prompt() -> None:
    assert len(CASES) == 3
    assert len({case.name for case in CASES}) == len(CASES)
    assert {case.user_prompt for case in CASES} == {USER_PROMPT}
    for case in CASES:
        assert case.baseline_files is case.files
        assert case.hidden_tests == case.hidden_oracle
        assert case.structural_expectations is case.expectations


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_materialization_excludes_hidden_oracle(case, tmp_path: Path) -> None:
    materialize(case, tmp_path)

    assert all(case.hidden_oracle not in path.read_text() for path in tmp_path.rglob("*.py"))
    assert not (tmp_path / "tests/oracle").exists()
    assert (tmp_path / "ARCHITECTURE.md").is_file()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_unmodified_baseline_does_not_satisfy_target_structure(case, tmp_path: Path) -> None:
    materialize(case, tmp_path)

    assert structural_failures(case, tmp_path)


def test_every_case_has_executable_hidden_oracle() -> None:
    for case in CASES:
        compile(case.hidden_oracle, f"{case.name}/tests/oracle/test_behavior.py", "exec")
        assert "def test_" in case.hidden_oracle
