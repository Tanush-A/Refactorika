from dataclasses import asdict

from eval.agents.schema import (
    AgentResult,
    PlanStep,
    Postcondition,
    RefactorPlan,
    TerminationReason,
    ToolEvent,
    WorkflowState,
)


def test_workflow_and_termination_contracts_are_stable() -> None:
    assert [state.value for state in WorkflowState] == [
        "discover",
        "select",
        "plan",
        "execute",
        "verify",
        "repair",
        "completion_audit",
        "done",
    ]
    assert {reason.value for reason in TerminationReason} == {
        "completed",
        "completed_after_repair",
        "safe_escalation",
        "iteration_limit",
        "agent_timeout",
        "provider_failure",
        "gate_failure",
        "malformed_response",
        "invalid_patch",
        "completion_audit_failure",
    }


def test_agent_result_serializes_plan_and_tool_trace() -> None:
    plan = RefactorPlan(
        objective="rename internal helper",
        rationale="remove ambiguous naming",
        affected_paths=["app/service.py"],
        expected_call_sites=["app/api.py"],
        compatibility_requirements=["preserve public export"],
        structural_postconditions=[Postcondition("defines", "app/service.py", "build_slug")],
        steps=[PlanStep("step-1", "rename helper", ["app/service.py", "app/api.py"])],
    )
    event = ToolEvent(
        arm="agentic+harness",
        case="rename",
        trial=0,
        sequence=1,
        tool="search_code",
        started_at="2026-06-21T00:00:00+00:00",
        seconds=0.01,
        status="ok",
        error_class=None,
        input_size=12,
        output_size=34,
    )
    result = AgentResult(
        edits={"app/service.py": "..."},
        termination_reason=TerminationReason.COMPLETED,
        model_calls=4,
        plan=plan,
        tool_events=[event],
    )

    encoded = asdict(result)
    assert encoded["plan"]["steps"][0]["id"] == "step-1"
    assert encoded["tool_events"][0]["tool"] == "search_code"
