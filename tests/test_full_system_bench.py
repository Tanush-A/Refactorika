from __future__ import annotations

import json
import threading
from pathlib import Path

import eval.full_system_bench as full_bench
from eval.agents.providers import ToolCompletion
from eval.full_system_bench import (
    CASES,
    AgenticBackend,
    AgenticHarnessBackend,
    Completion,
    Pricing,
    Usage,
    adapt_case,
    build_harness_prompt,
    calibrate,
    materialize,
    oracle_grade,
    propose_agentic,
    run,
    visible_snapshot,
)
from eval.full_system_cases.behavior import GUARD_CLAUSES
from refactorika.harness import GateChecks, VerificationRecord

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
    assert len(CASES) == 49
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
    assert result["meta"]["schema_version"] == 3
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


class ScriptedHarnessBackend:
    """Test double: returns a known-good edit via the agentic+harness interface."""

    name = "scripted+harness"

    def run(
        self, repo: Path, user_prompt: str
    ) -> tuple[dict[str, str], Usage, float, "str | None", int, list[dict]]:
        content = (
            "from collections.abc import Iterable\n\n\n"
            "def billable_event_ids(events: Iterable[dict[str, object]]) -> list[str]:\n"
            "    selected: list[str] = []\n"
            "    for event in events:\n"
            "        if event.get('enabled') is not True:\n"
            "            continue\n"
            "        if event.get('kind') == 'heartbeat':\n"
            "            continue\n"
            "        event_id = event.get('id')\n"
            "        if not (isinstance(event_id, str) and event_id):\n"
            "            continue\n"
            "        selected.append(event_id)\n"
            "    return selected\n"
        )
        edits = {"app/events.py": content}
        return edits, Usage(), 0.0, None, 1, []


def test_agentic_mcp_arm_appears_in_records() -> None:
    backend = ScriptedBackend()
    mcp_backend = ScriptedHarnessBackend()
    result = run(
        backend,
        (adapt_case(GUARD_CLAUSES),),
        trials=1,
        max_retries=0,
        agentic_mcp_backend=mcp_backend,
    )
    arms = {r["arm"] for r in result["records"]}
    assert "agentic+harness" in arms
    assert "paired_agentic_harness_vs_off" in result["aggregate"]
    assert "paired_agentic_harness_vs_agentic" in result["aggregate"]
    mcp_record = next(r for r in result["records"] if r["arm"] == "agentic+harness")
    assert mcp_record["status"] == "shipped"
    assert "tokens" in mcp_record
    assert "timing" in mcp_record
    assert "end_to_end_seconds" in mcp_record["timing"]
    assert "change" in mcp_record
    assert "gate_log" in mcp_record
    assert "gate_calls" in mcp_record


class NeverStoppingProvider:
    def __init__(self) -> None:
        self.call = 0

    def complete_tools(self, *args, **kwargs) -> ToolCompletion:
        del args, kwargs
        self.call += 1
        return ToolCompletion(
            [
                {
                    "type": "tool_use",
                    "id": f"tool-{self.call}",
                    "name": "list_files",
                    "input": {},
                }
            ],
            Usage(1, 1),
            0.01,
        )


class NeverStoppingAgent(AgenticBackend):
    def __init__(self) -> None:
        super().__init__("scripted", "unused", max_iterations=2, agent_timeout=60)
        self._provider = NeverStoppingProvider()


def test_agentic_backends_share_one_loop_implementation() -> None:
    assert AgenticBackend._run_shared is AgenticHarnessBackend._run_shared


def test_agentic_iteration_limit_is_an_infrastructure_failure(tmp_path: Path) -> None:
    case = adapt_case(GUARD_CLAUSES)
    repo = materialize(case, tmp_path)

    proposal = propose_agentic(NeverStoppingAgent(), case, repo)

    assert proposal.error_class == "iteration_limit"
    assert proposal.error == "iteration_limit_exceeded: reached 2 model calls"
    assert proposal.model_calls == 2
    assert proposal.agent_result is not None
    assert proposal.agent_result.metadata["phase_tokens"]["discover"] == 4


def test_gate_crash_invalidates_run_without_repairing(monkeypatch) -> None:
    backend = ScriptedBackend()

    def crashed_gate(*args, **kwargs) -> VerificationRecord:
        del args, kwargs
        return VerificationRecord(
            files=["app/events.py"],
            checks=GateChecks(parse=True, lint=True),
            status="rolled-back",
            failure_reason="gate_crash: TimeoutExpired",
            gate_details={"gate_crash": "TimeoutExpired"},
        )

    monkeypatch.setattr(full_bench, "verify_edits", crashed_gate)

    result = run(
        backend,
        (adapt_case(GUARD_CLAUSES),),
        trials=1,
        max_retries=2,
    )

    assert result["status"] == "invalid-infrastructure"
    assert len(backend.prompts) == 2
    assert result["aggregate"]["reliability"]["gate_failures"] == 1


class ParallelCompletionBackend:
    name = "parallel-scripted"

    def __init__(self, barrier: threading.Barrier) -> None:
        self.barrier = barrier

    def complete(self, prompt: str) -> Completion:
        del prompt
        self.barrier.wait(timeout=2)
        return Completion(json.dumps(VALID_GUARD_REFACTOR), Usage(1, 1), 0.01)


class ParallelAgentBackend:
    name = "parallel-agent"

    def __init__(self, barrier: threading.Barrier, *, harness: bool = False) -> None:
        self.barrier = barrier
        self.harness = harness

    def run(self, repo: Path, user_prompt: str):
        del user_prompt
        self.barrier.wait(timeout=2)
        (repo / "app/events.py").write_text(VALID_GUARD_REFACTOR["app/events.py"])
        base = (VALID_GUARD_REFACTOR, Usage(1, 1), 0.01, None, 1)
        return (*base, []) if self.harness else base


def test_parallel_mode_starts_all_four_agents_concurrently() -> None:
    barrier = threading.Barrier(4)
    progress: list[str] = []
    result = run(
        ParallelCompletionBackend(barrier),
        (adapt_case(GUARD_CLAUSES),),
        trials=1,
        max_retries=0,
        agentic_backend=ParallelAgentBackend(barrier),
        agentic_mcp_backend=ParallelAgentBackend(barrier, harness=True),
        parallel_arms=True,
        progress=progress.append,
    )

    assert {record["arm"] for record in result["records"]} == {
        "off",
        "on",
        "agentic",
        "agentic+harness",
    }
    assert all(record["execution"]["parallel_arms"] for record in result["records"])
    assert all(not record["execution"]["sequential_fallback"] for record in result["records"])
    assert any("case=guard_clause_continue start mode=parallel" in event for event in progress)
    assert sum(" complete status=" in event for event in progress) == 4


class FailOnceParallelBackend:
    name = "fail-once"

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0

    def complete(self, prompt: str) -> Completion:
        del prompt
        with self.lock:
            self.calls += 1
            call = self.calls
        if call == 1:
            return Completion("", Usage(), 0.01, "temporary 429", "provider_failure")
        return Completion(json.dumps(VALID_GUARD_REFACTOR), Usage(1, 1), 0.01)


def test_parallel_provider_failure_retries_only_failed_arm_sequentially() -> None:
    backend = FailOnceParallelBackend()
    result = run(
        backend,
        (adapt_case(GUARD_CLAUSES),),
        trials=1,
        max_retries=0,
        parallel_arms=True,
        parallel_fallback_delay=0,
    )

    assert result["status"] == "valid"
    assert backend.calls == 3
    fallback = result["records"][0]["execution"]
    assert fallback["sequential_fallback"] is True
    assert len(fallback["fallback_arms"]) == 1


def test_parallel_iteration_limit_is_not_retried_sequentially() -> None:
    result = run(
        ScriptedBackend(),
        (adapt_case(GUARD_CLAUSES),),
        trials=1,
        max_retries=0,
        agentic_backend=NeverStoppingAgent(),
        parallel_arms=True,
        parallel_fallback_delay=0,
    )

    agentic = next(record for record in result["records"] if record["arm"] == "agentic")
    assert result["status"] == "invalid-infrastructure"
    assert agentic["error_class"] == "iteration_limit"
    assert agentic["usage"]["model_calls"] == 2
    assert agentic["execution"]["sequential_fallback"] is False
