from collections.abc import Iterator

import pytest
from eval.agents.loop import (
    AgentLoop,
    AgentTimeoutError,
    CompletionAuditError,
    GateFailureError,
    InvalidPatchError,
    InvalidTransitionError,
    LoopAction,
    LoopBudgets,
    MalformedResponseError,
    ProviderFailureError,
    validate_transition,
)
from eval.agents.schema import TerminationReason, WorkflowState


def scripted(actions: list[LoopAction]):
    iterator: Iterator[LoopAction] = iter(actions)

    def driver(_state: WorkflowState, _context: object) -> LoopAction:
        return next(iterator)

    return driver


def happy_path(*, repair: bool = False) -> list[LoopAction]:
    actions = [
        LoopAction(WorkflowState.SELECT, model_calls=1),
        LoopAction(WorkflowState.PLAN),
        LoopAction(WorkflowState.EXECUTE, model_calls=1),
        LoopAction(WorkflowState.VERIFY, model_calls=1, edits={"app.py": "new"}),
    ]
    if repair:
        actions.extend(
            [
                LoopAction(WorkflowState.REPAIR),
                LoopAction(WorkflowState.VERIFY, model_calls=1),
            ]
        )
    actions.extend(
        [
            LoopAction(WorkflowState.COMPLETION_AUDIT),
            LoopAction(WorkflowState.DONE, model_calls=1),
        ]
    )
    return actions


def test_enforces_complete_workflow_before_done() -> None:
    result = AgentLoop(scripted(happy_path())).run()

    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.edits == {"app.py": "new"}
    assert result.model_calls == 4
    assert result.metadata["calls_before_first_edit"] == 3
    assert result.metadata["phase_tokens"] == {
        "completion_audit": 0,
        "discover": 0,
        "execute": 0,
        "plan": 0,
        "select": 0,
        "verify": 0,
    }
    assert result.metadata["visited_states"] == [
        "discover",
        "select",
        "plan",
        "execute",
        "verify",
        "completion_audit",
        "done",
    ]


def test_repair_path_has_distinct_completion_reason() -> None:
    result = AgentLoop(scripted(happy_path(repair=True))).run()

    assert result.termination_reason is TerminationReason.COMPLETED_AFTER_REPAIR
    assert result.metadata["phase_calls"]["repair"] == 1


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (WorkflowState.DISCOVER, WorkflowState.EXECUTE),
        (WorkflowState.SELECT, WorkflowState.EXECUTE),
        (WorkflowState.PLAN, WorkflowState.DONE),
        (WorkflowState.VERIFY, WorkflowState.DONE),
    ],
)
def test_invalid_transitions_are_rejected(current: WorkflowState, requested: WorkflowState) -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition(current, requested)


def test_loop_classifies_transition_attempt_as_malformed_response() -> None:
    result = AgentLoop(scripted([LoopAction(WorkflowState.EXECUTE, model_calls=1)])).run()

    assert result.termination_reason is TerminationReason.MALFORMED_RESPONSE
    assert "invalid workflow transition" in (result.error or "")


def test_discovery_budget_is_enforced() -> None:
    actions = [LoopAction(WorkflowState.DISCOVER, model_calls=1) for _ in range(9)]

    result = AgentLoop(scripted(actions)).run()

    assert result.termination_reason is TerminationReason.ITERATION_LIMIT
    assert result.model_calls == 8
    assert result.metadata["phase_calls"] == {"discover": 8}


def test_total_call_budget_is_enforced() -> None:
    budgets = LoopBudgets(total_calls=2)
    actions = [
        LoopAction(WorkflowState.DISCOVER, model_calls=2),
        LoopAction(WorkflowState.SELECT, model_calls=1),
    ]

    result = AgentLoop(scripted(actions), budgets).run()

    assert result.termination_reason is TerminationReason.ITERATION_LIMIT


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (AgentTimeoutError("slow"), TerminationReason.AGENT_TIMEOUT),
        (TimeoutError("slow"), TerminationReason.AGENT_TIMEOUT),
        (ProviderFailureError("provider"), TerminationReason.PROVIDER_FAILURE),
        (GateFailureError("gate"), TerminationReason.GATE_FAILURE),
        (InvalidPatchError("patch"), TerminationReason.INVALID_PATCH),
        (
            CompletionAuditError("incomplete"),
            TerminationReason.COMPLETION_AUDIT_FAILURE,
        ),
        (MalformedResponseError("bad"), TerminationReason.MALFORMED_RESPONSE),
    ],
)
def test_failure_classification(exception: Exception, reason: TerminationReason) -> None:
    def fail(_state: WorkflowState, _context: object) -> LoopAction:
        raise exception

    result = AgentLoop(fail).run()

    assert result.termination_reason is reason
    assert result.error == str(exception)


def test_safe_escalation_can_stop_without_completion() -> None:
    result = AgentLoop(
        scripted(
            [
                LoopAction(
                    WorkflowState.SELECT,
                    model_calls=1,
                    termination_reason=TerminationReason.SAFE_ESCALATION,
                    error="ambiguous target",
                )
            ]
        )
    ).run()

    assert result.termination_reason is TerminationReason.SAFE_ESCALATION
    assert result.error == "ambiguous target"


def test_completed_reason_cannot_bypass_completion_audit() -> None:
    result = AgentLoop(
        scripted(
            [
                LoopAction(
                    WorkflowState.SELECT,
                    termination_reason=TerminationReason.COMPLETED,
                )
            ]
        )
    ).run()

    assert result.termination_reason is TerminationReason.MALFORMED_RESPONSE


def test_explicit_completion_records_done_state() -> None:
    actions = happy_path()
    actions[-1].termination_reason = TerminationReason.COMPLETED

    result = AgentLoop(scripted(actions)).run()

    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.metadata["final_state"] == "done"


def test_non_action_driver_result_is_malformed() -> None:
    def malformed(_state: WorkflowState, _context: object):
        return {"next_state": "select"}

    result = AgentLoop(malformed).run()  # type: ignore[arg-type]

    assert result.termination_reason is TerminationReason.MALFORMED_RESPONSE
