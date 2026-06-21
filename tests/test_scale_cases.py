from __future__ import annotations

from pathlib import Path

import pytest
from eval.full_system_bench import adapt_case, calibrate, materialize, oracle_grade
from eval.full_system_cases.scale import (
    SCALE_CASES,
    ScaleCase,
    bad_control_edits,
    build_scale_case,
    reference_edits,
)


def _production_paths(case: ScaleCase) -> set[str]:
    return {
        path
        for path in case.baseline_files
        if path.endswith(".py") and not path.startswith("tests/")
    }


def _apply(repo: Path, edits: dict[str, str]) -> None:
    for relative, content in edits.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_scale_repositories_have_exact_counts_and_target_density() -> None:
    medium, large = SCALE_CASES

    assert len(_production_paths(medium)) == medium.source_file_count == 20
    assert len(_production_paths(large)) == large.source_file_count == 100
    assert 1_200 <= medium.source_loc <= 1_800
    assert 6_500 <= large.source_loc <= 8_500
    assert medium.size_tier == "medium"
    assert large.size_tier == "large"


def test_scale_generation_is_deterministic_and_keeps_the_same_semantic_core() -> None:
    rebuilt = (build_scale_case(20), build_scale_case(100))
    assert [case.fixture_hash for case in rebuilt] == [case.fixture_hash for case in SCALE_CASES]

    medium, large = SCALE_CASES
    shared_paths = set(medium.baseline_files) & set(large.baseline_files)
    relevant = {
        path
        for path in shared_paths
        if path.startswith("app/legacy/")
        or path.startswith("app/shared/")
        or path.startswith("app/consumers/")
        or path in {"app/public.py", "app/registry.py", "vendor/generated_phone.py"}
    }
    assert relevant
    assert all(medium.baseline_files[path] == large.baseline_files[path] for path in relevant)


def test_scale_metadata_accounts_for_relevant_and_distractor_sources() -> None:
    for case in SCALE_CASES:
        metadata = case.benchmark_metadata
        assert metadata["source_file_count"] == case.source_file_count
        assert case.relevant_file_count + case.distractor_file_count == case.source_file_count
        assert len(case.fixture_hash) == 64


def test_hidden_oracle_is_not_part_of_the_materialized_baseline(tmp_path: Path) -> None:
    case = SCALE_CASES[0]
    repo = materialize(adapt_case(case), tmp_path / "repo")

    assert not (repo / "tests/oracle/test_hidden.py").exists()
    assert "test_all_caller_shapes_use_the_canonical_policy" not in "".join(
        case.baseline_files.values()
    )


def test_scale_baselines_calibrate() -> None:
    result = calibrate(tuple(adapt_case(case) for case in SCALE_CASES))
    assert result["valid"] is True


@pytest.mark.parametrize("case", SCALE_CASES, ids=lambda case: case.size_tier)
def test_reference_patch_passes_behavior_and_structure(case: ScaleCase, tmp_path: Path) -> None:
    adapted = adapt_case(case)
    repo = materialize(adapted, tmp_path / case.size_tier)
    _apply(repo, reference_edits(case))

    behavior, _, structure = oracle_grade(adapted, repo)
    assert behavior
    assert structure == []


@pytest.mark.parametrize(
    "control",
    [
        "missed_alias_caller",
        "broken_legacy_contract",
        "leading_zero_boundary",
        "protected_vendor_edit",
    ],
)
def test_bad_controls_are_rejected(control: str, tmp_path: Path) -> None:
    case = SCALE_CASES[0]
    adapted = adapt_case(case)
    repo = materialize(adapted, tmp_path / control)
    _apply(repo, bad_control_edits(case)[control])

    behavior, _, structure = oracle_grade(adapted, repo)
    assert not (behavior and not structure)
