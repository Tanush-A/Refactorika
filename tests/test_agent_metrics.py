from __future__ import annotations

from eval.agents.metrics import aggregate_agent_metrics, collect_agent_metrics
from eval.agents.schema import (
    AgentResult,
    PlanStep,
    RefactorPlan,
    TerminationReason,
    ToolEvent,
)


def event(sequence: int, tool: str, status: str = "ok") -> ToolEvent:
    return ToolEvent(
        arm="agentic+harness",
        case="rename",
        trial=1,
        sequence=sequence,
        tool=tool,
        started_at="2026-01-01T00:00:00Z",
        seconds=0.1,
        status=status,
        error_class=None if status == "ok" else "GateFailure",
        input_size=10,
        output_size=20,
    )


def plan() -> RefactorPlan:
    return RefactorPlan(
        objective="rename",
        rationale="clarity",
        affected_paths=["a.py"],
        expected_call_sites=[],
        compatibility_requirements=[],
        structural_postconditions=[],
        steps=[
            PlanStep("one", "rename", ["a.py"], completed=True),
            PlanStep("two", "call sites", ["b.py"]),
        ],
    )


def test_collects_requested_observability() -> None:
    result = AgentResult(
        edits={"a.py": "x = 1\n"},
        termination_reason=TerminationReason.COMPLETED_AFTER_REPAIR,
        model_calls=7,
        input_tokens=900,
        output_tokens=200,
        plan=plan(),
        tool_events=[
            event(1, "list_files"),
            event(2, "search_code"),
            event(3, "apply_and_verify_multi", "error"),
            event(4, "apply_and_verify_multi"),
            event(5, "completion_audit"),
        ],
        metadata={
            "calls_before_plan": 2,
            "calls_before_first_edit": 4,
            "rollbacks": 1,
            "repair_attempts": 1,
            "successful_repairs": 1,
            "completion_audit_failures": 0,
            "phase_tokens": {"discover": 300, "execute": 800},
            "final_diff_lines": 12,
            "final_diff_bytes": 420,
            "final_diff_files": 2,
            "sequential_fallback": False,
        },
    )
    metrics = collect_agent_metrics(result)
    assert metrics.termination_reason == "completed_after_repair"
    assert metrics.calls_before_plan == 2
    assert metrics.first_edit_event_sequence == 3
    assert metrics.tool_counts["apply_and_verify_multi"] == 2
    assert metrics.tool_error_counts == {"apply_and_verify_multi": 1}
    assert metrics.gate_calls == 3
    assert metrics.repair_effectiveness == 1.0
    assert metrics.plan_steps_completed == 1
    assert metrics.plan_step_completion_rate == 0.5
    assert metrics.tokens_by_phase == {"discover": 300, "execute": 800}
    assert metrics.sequential_fallback is False
    assert metrics.uninstrumented_fields == ()


def test_missing_instrumentation_is_explicit_not_zero() -> None:
    metrics = collect_agent_metrics(
        AgentResult(
            edits={},
            termination_reason=TerminationReason.ITERATION_LIMIT,
            model_calls=10,
        )
    )
    assert metrics.calls_before_plan is None
    assert metrics.rollbacks is None
    assert metrics.tokens_by_phase is None
    assert metrics.sequential_fallback is None
    assert "rollbacks" in metrics.uninstrumented_fields
    assert "tokens_by_phase" in metrics.uninstrumented_fields
    assert metrics.tool_counts == {}
    assert metrics.gate_calls == 0
    assert metrics.plan_steps_total is None


def test_zero_repairs_does_not_claim_effectiveness() -> None:
    result = AgentResult(
        edits={},
        termination_reason=TerminationReason.COMPLETED,
        model_calls=2,
        metadata={"repair_attempts": 0, "successful_repairs": 0},
    )
    assert collect_agent_metrics(result).repair_effectiveness is None


def test_rollbacks_and_audit_failures_can_be_derived_from_events() -> None:
    result = AgentResult(
        edits={},
        termination_reason=TerminationReason.COMPLETION_AUDIT_FAILURE,
        model_calls=3,
        tool_events=[
            event(1, "apply_and_verify_multi", "rolled-back"),
            event(2, "completion_audit", "failed"),
        ],
    )
    metrics = collect_agent_metrics(result)
    assert metrics.rollbacks == 1
    assert metrics.completion_audit_failures == 1


def test_aggregate_preserves_records_and_sums_counts() -> None:
    first = AgentResult(
        edits={},
        termination_reason=TerminationReason.COMPLETED,
        model_calls=2,
        input_tokens=10,
        output_tokens=5,
        tool_events=[event(1, "search_code")],
    )
    second = AgentResult(
        edits={},
        termination_reason=TerminationReason.PROVIDER_FAILURE,
        model_calls=1,
        input_tokens=3,
        output_tokens=1,
        tool_events=[event(1, "search_code"), event(2, "run_tests", "error")],
    )
    aggregate = aggregate_agent_metrics([first, second])
    assert aggregate["runs"] == 2
    assert aggregate["model_calls"] == 3
    assert aggregate["input_tokens"] == 13
    assert aggregate["tool_counts"] == {"run_tests": 1, "search_code": 2}
    assert aggregate["termination_reasons"] == {"completed": 1, "provider_failure": 1}
    assert len(aggregate["records"]) == 2


def test_invalid_metadata_values_are_reported_missing() -> None:
    result = AgentResult(
        edits={},
        termination_reason=TerminationReason.COMPLETED,
        model_calls=1,
        metadata={
            "calls_before_plan": "two",
            "phase_tokens": {"discover": -1, "execute": "many"},
            "sequential_fallback": 1,
        },
    )
    metrics = collect_agent_metrics(result)
    assert metrics.calls_before_plan is None
    assert metrics.tokens_by_phase is None
    assert metrics.sequential_fallback is None
