from __future__ import annotations

from eval.full_system_bench import adapt_case, calibrate, grade_structure, materialize
from eval.full_system_cases import (
    CONTRACT_STRESS_CASES,
    SEMANTIC_STRESS_CASES,
    STRESS_CASES,
    SYSTEM_STRESS_CASES,
)

ALL_STRESS_CASES = (
    *STRESS_CASES,
    *SEMANTIC_STRESS_CASES,
    *CONTRACT_STRESS_CASES,
    *SYSTEM_STRESS_CASES,
)


def test_stress_case_names_and_categories_are_unique() -> None:
    names = [case.name for case in ALL_STRESS_CASES]
    assert len(names) == 38
    assert len(names) == len(set(names))
    assert len({case.category for case in ALL_STRESS_CASES}) >= 8
    assert {case.user_prompt for case in ALL_STRESS_CASES} == {"refactor this codebase"}
    assert all("ARCHITECTURE.md" in case.baseline_files for case in ALL_STRESS_CASES)


def test_stress_baselines_are_behaviorally_valid_and_have_refactor_headroom() -> None:
    result = calibrate(tuple(adapt_case(case) for case in ALL_STRESS_CASES))

    assert result["valid"] is True
    assert all(record["visible_baseline_pass"] for record in result["records"])
    assert all(record["hidden_baseline_pass"] for record in result["records"])
    assert all(record["baseline_misses_target_structure"] for record in result["records"])


def test_generated_decoy_is_a_protected_structural_contract(tmp_path) -> None:
    case = next(case for case in STRESS_CASES if case.name == "generated_vendor_decoy_unchanged")
    adapted = adapt_case(case)
    repo = materialize(adapted, tmp_path)

    generated = repo / "vendor/generated_slug.py"
    generated.write_text(generated.read_text() + "\n# accidental edit\n")

    assert "changed protected path vendor/generated_slug.py" in grade_structure(adapted, repo)


def test_calls_private_requires_one_shared_helper(tmp_path) -> None:
    case = next(case for case in STRESS_CASES if case.name == "stable_sort_tie_order")
    adapted = adapt_case(case)
    repo = materialize(adapted, tmp_path)
    (repo / "app/ranking.py").write_text(
        "def _active_key(item):\n    return -int(item['score'])\n\n"
        "def _all_key(item):\n    return -int(item['score'])\n\n"
        "def rank_active(items):\n"
        "    return sorted([item for item in items if item.get('active') is True], "
        "key=_active_key)\n\n"
        "def rank_all(items):\n    return sorted(items, key=_all_key)\n"
    )

    assert "no shared private helper across public functions in app/ranking.py" in (
        grade_structure(adapted, repo)
    )
