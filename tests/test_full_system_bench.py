from __future__ import annotations

import json
from pathlib import Path

from eval.full_system_bench import (
    CASES,
    Completion,
    Pricing,
    Usage,
    adapt_case,
    build_harness_prompt,
    calibrate,
    materialize,
    oracle_grade,
    run,
    visible_snapshot,
)
from eval.full_system_cases.behavior import GUARD_CLAUSES

VALID_GUARD_REFACTOR = {
    "app/events.py": """from collections.abc import Iterable


def billable_event_ids(events: Iterable[dict[str, object]]) -> list[str]:
    selected: list[str] = []
    for event in events:
        if event.get("enabled") is not True:
            continue
        if event.get("kind") == "heartbeat":
            continue
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            continue
        selected.append(event_id)
    return selected
"""
}


class ScriptedBackend:
    name = "scripted"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> Completion:
        self.prompts.append(prompt)
        if "planning stage" in prompt:
            text = "Flatten the nested event filters into loop-level guard clauses."
        else:
            text = json.dumps(VALID_GUARD_REFACTOR)
        return Completion(text, Usage(10, 5), 0.01)


def test_case_adapter_normalizes_all_fixture_families() -> None:
    assert len(CASES) == 9
    assert {case.user_prompt for case in CASES} == {"refactor this codebase"}
    assert all(case.hidden_tests for case in CASES)
    assert all(path.startswith("tests/oracle/") for case in CASES for path in case.hidden_tests)


def test_snapshot_and_harness_prompt_never_include_hidden_oracle(tmp_path: Path) -> None:
    case = adapt_case(GUARD_CLAUSES)
    repo = materialize(case, tmp_path)

    assert not any(path.startswith("tests/oracle/") for path in visible_snapshot(repo))
    prompt = build_harness_prompt(case, repo)
    assert "test_invalid_item_does_not_abort_later_items" not in prompt
    assert "refactor this codebase" in prompt


def test_full_system_arms_generate_independent_proposals_from_same_user_request() -> None:
    backend = ScriptedBackend()
    result = run(backend, (adapt_case(GUARD_CLAUSES),), trials=1, max_retries=0)

    assert result["status"] == "valid"
    assert len(backend.prompts) == 2  # one independent initial call per arm
    assert all(
        record["initial_user_prompt"] == "refactor this codebase" for record in result["records"]
    )
    assert {record["arm"] for record in result["records"]} == {"off", "on"}
    assert all(record["correct_landed"] for record in result["records"])
    assert "audit_plan" in result["records"][1]["harness_prompt"]
    assert "audit_plan" not in backend.prompts[0]
    assert result["meta"]["schema_version"] == 2
    assert result["meta"]["initial_model_calls_per_arm"] == 1
    assert all(record["usage"]["model_calls"] == 1 for record in result["records"])
    assert all(record["usage"]["input_tokens"] == 10 for record in result["records"])
    assert all("end_to_end_seconds" in record["timing"] for record in result["records"])
    assert result["aggregate"]["paired_final"] == {
        "on_wins": 0,
        "off_wins": 0,
        "ties": 1,
        "on_minus_off_ci95_case_clustered": [0.0, 0.0],
    }


def test_full_system_calibration_requires_behavioral_baseline_and_refactor_headroom() -> None:
    result = calibrate((adapt_case(GUARD_CLAUSES),))

    assert result["valid"] is True
    assert result["records"][0]["visible_baseline_pass"] is True
    assert result["records"][0]["hidden_baseline_pass"] is True
    assert result["records"][0]["baseline_misses_target_structure"] is True


def test_grader_separates_behavior_regressions_from_incomplete_refactors(
    tmp_path: Path,
) -> None:
    case = adapt_case(GUARD_CLAUSES)
    repo = materialize(case, tmp_path)
    path = repo / "app/events.py"
    path.write_text(path.read_text() + "\n# Behavior preserved, target structure still missing.\n")

    behavior_pass, _, structural_failures = oracle_grade(case, repo)

    assert behavior_pass is True
    assert structural_failures == ["expected loop guard clauses using continue"]


class RepairBackend:
    name = "repair-scripted"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, _prompt: str) -> Completion:
        self.calls += 1
        if self.calls == 2:  # first ON proposal after the successful OFF call
            return Completion('{"app/events.py": "def broken(:\\n"}', Usage(10, 5), 0.01)
        return Completion(json.dumps(VALID_GUARD_REFACTOR), Usage(10, 5), 0.01)


def test_initial_and_final_outcomes_split_repairs_and_account_for_calls() -> None:
    backend = RepairBackend()
    result = run(
        backend,
        (adapt_case(GUARD_CLAUSES),),
        trials=1,
        max_retries=1,
        pricing=Pricing(input_per_mtok=1, output_per_mtok=2),
    )

    on = next(record for record in result["records"] if record["arm"] == "on")
    assert on["initial"]["correct_landed"] is False
    assert on["correct_landed"] is True
    assert on["usage"]["model_calls"] == 2
    assert on["usage"]["input_tokens"] == 20
    assert on["usage"]["output_tokens"] == 10
    assert on["usage"]["cost_dollars"] == 0.00004
    assert result["aggregate"]["paired_initial"]["on_wins"] == 0
    assert result["aggregate"]["paired_final"]["ties"] == 1
    assert result["aggregate"]["harness"]["initial_rejections"] == 1
    assert result["aggregate"]["harness"]["repair_successes"] == 1
    assert result["aggregate"]["harness"]["rejections_by_gate"] == {"parse": 1}
    assert result["aggregate"]["harness"]["rollback_integrity_failures"] == 0


class ConfigurationFailureBackend:
    name = "unconfigured"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, _prompt: str) -> Completion:
        self.calls += 1
        return Completion("", Usage(), 0.0, "API key is not configured", "configuration_failure")


def test_provider_configuration_failure_invalidates_run_without_retrying() -> None:
    backend = ConfigurationFailureBackend()

    result = run(backend, (adapt_case(GUARD_CLAUSES),), trials=1, max_retries=2)

    assert result["status"] == "invalid-infrastructure"
    assert backend.calls == 2  # one attempt per arm; configuration retries cannot help
    assert result["aggregate"]["reliability"]["configuration_failures"] == 2
