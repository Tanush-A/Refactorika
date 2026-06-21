from eval.full_system_cases import (
    ALL_CASES,
    BEHAVIOR_CASES,
    CONTRACT_STRESS_CASES,
    MULTIFILE_CASES,
    RECOVERY_CASES,
    SEMANTIC_STRESS_CASES,
    STRESS_CASES,
    SYSTEM_STRESS_CASES,
    USER_PROMPT,
)


def test_registry_contains_non_overlapping_case_families() -> None:
    assert len(BEHAVIOR_CASES) == 3
    assert len(MULTIFILE_CASES) == 3
    assert len(RECOVERY_CASES) == 3
    assert len(STRESS_CASES) == 8
    assert len(SEMANTIC_STRESS_CASES) == 10
    assert len(CONTRACT_STRESS_CASES) == 10
    assert len(SYSTEM_STRESS_CASES) == 10
    assert len(ALL_CASES) == 47
    assert len({case.name for case in ALL_CASES}) == 47


def test_every_case_starts_from_the_exact_same_generic_prompt() -> None:
    assert USER_PROMPT == "refactor this codebase"
    assert {case.user_prompt for case in ALL_CASES} == {USER_PROMPT}


def test_every_case_exposes_runner_metadata() -> None:
    for case in ALL_CASES:
        assert case.baseline_files
        assert case.hidden_tests
        assert case.structural_expectations
