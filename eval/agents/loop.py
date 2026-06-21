"""Provider- and tool-agnostic workflow for refactoring agents.

The loop owns workflow policy and accounting.  A driver owns model/provider calls
and tool execution, then returns a :class:`LoopAction` describing the outcome of
one orchestration step.  This keeps the control and harness arms on the same
state machine without coupling either one to a provider or tool implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from eval.agents.schema import (
    AgentResult,
    RefactorPlan,
    TerminationReason,
    ToolEvent,
    WorkflowState,
)


class AgentLoopError(Exception):
    """Base class for failures classified by the shared loop."""


class AgentTimeoutError(AgentLoopError):
    """The overall agent deadline or a provider/tool deadline was exceeded."""


class ProviderFailureError(AgentLoopError):
    """The model provider failed without producing a usable response."""


class MalformedResponseError(AgentLoopError):
    """The provider response could not be interpreted as a loop action."""


class InvalidPatchError(AgentLoopError):
    """A proposed patch failed validation before verification."""


class GateFailureError(AgentLoopError):
    """Repository verification failed and cannot be repaired."""


class CompletionAuditError(AgentLoopError):
    """The final plan/diff audit failed and cannot be repaired."""


class InvalidTransitionError(MalformedResponseError):
    """A driver requested a transition forbidden by the workflow."""


@dataclass(frozen=True)
class LoopBudgets:
    """Maximum model calls by phase and across the entire run."""

    discovery_calls: int = 8
    planning_calls: int = 4
    execution_calls: int = 20
    repair_calls: int = 8
    completion_audit_calls: int = 3
    total_calls: int = 30
    timeout_seconds: float = 900.0

    def limit_for(self, state: WorkflowState) -> int | None:
        return {
            WorkflowState.DISCOVER: self.discovery_calls,
            WorkflowState.PLAN: self.planning_calls,
            WorkflowState.EXECUTE: self.execution_calls,
            WorkflowState.REPAIR: self.repair_calls,
            WorkflowState.COMPLETION_AUDIT: self.completion_audit_calls,
        }.get(state)


@dataclass
class LoopContext:
    """Read-only-by-convention run state supplied to the driver."""

    state: WorkflowState = WorkflowState.DISCOVER
    model_calls: int = 0
    phase_calls: dict[WorkflowState, int] = field(default_factory=dict)
    visited_states: list[WorkflowState] = field(default_factory=lambda: [WorkflowState.DISCOVER])
    edits: dict[str, str] = field(default_factory=dict)
    plan: RefactorPlan | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopAction:
    """Outcome returned by a driver for one workflow step."""

    next_state: WorkflowState
    model_calls: int = 0
    edits: dict[str, str] = field(default_factory=dict)
    plan: RefactorPlan | None = None
    tool_events: list[ToolEvent] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    termination_reason: TerminationReason | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


LoopDriver = Callable[[WorkflowState, LoopContext], LoopAction]


_ALLOWED_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.DISCOVER: frozenset((WorkflowState.DISCOVER, WorkflowState.SELECT)),
    WorkflowState.SELECT: frozenset((WorkflowState.PLAN,)),
    # PLAN may retry itself when a provider returns a structurally invalid plan.
    # The retry remains bounded by ``planning_calls`` and ``total_calls``.
    WorkflowState.PLAN: frozenset((WorkflowState.PLAN, WorkflowState.EXECUTE)),
    WorkflowState.EXECUTE: frozenset(
        (WorkflowState.EXECUTE, WorkflowState.PLAN, WorkflowState.VERIFY)
    ),
    WorkflowState.VERIFY: frozenset(
        (WorkflowState.EXECUTE, WorkflowState.REPAIR, WorkflowState.COMPLETION_AUDIT)
    ),
    WorkflowState.REPAIR: frozenset(
        (WorkflowState.REPAIR, WorkflowState.PLAN, WorkflowState.VERIFY)
    ),
    WorkflowState.COMPLETION_AUDIT: frozenset((WorkflowState.REPAIR, WorkflowState.DONE)),
    WorkflowState.DONE: frozenset(),
}


def validate_transition(current: WorkflowState, requested: WorkflowState) -> None:
    """Reject transitions that bypass selection, planning, or completion checks."""

    if requested not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"invalid workflow transition: {current} -> {requested}")


class AgentLoop:
    """Run the enforced refactoring workflow using a caller-supplied driver."""

    def __init__(self, driver: LoopDriver, budgets: LoopBudgets | None = None) -> None:
        self._driver = driver
        self._budgets = budgets or LoopBudgets()

    def run(self) -> AgentResult:
        started = monotonic()
        context = LoopContext()
        events: list[ToolEvent] = []
        token_totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        phase_tokens: dict[str, int] = {}
        terminal_reason: TerminationReason | None = None
        error: str | None = None

        try:
            while context.state is not WorkflowState.DONE:
                self._check_timeout(started)
                self._check_call_budget(context)
                current = context.state
                action = self._driver(current, context)
                if not isinstance(action, LoopAction):
                    raise MalformedResponseError("agent driver did not return LoopAction")
                if action.model_calls < 0:
                    raise MalformedResponseError("model_calls cannot be negative")

                self._account_calls(context, current, action.model_calls)
                action_tokens = sum(getattr(action, key) for key in token_totals)
                phase_tokens[current.value] = phase_tokens.get(current.value, 0) + action_tokens
                events.extend(action.tool_events)
                if action.plan is not None and "calls_before_plan" not in context.metadata:
                    context.metadata["calls_before_plan"] = context.model_calls
                if action.edits and "calls_before_first_edit" not in context.metadata:
                    context.metadata["calls_before_first_edit"] = context.model_calls
                if current is WorkflowState.REPAIR and action.model_calls:
                    context.metadata["repair_attempts"] = (
                        int(context.metadata.get("repair_attempts", 0)) + 1
                    )
                if action.metadata.get("campaign_replanned"):
                    context.edits.clear()
                    context.plan = None
                context.edits.update(action.edits)
                context.plan = action.plan or context.plan
                context.metadata.update(action.metadata)
                for key in token_totals:
                    token_totals[key] += getattr(action, key)

                if action.termination_reason is not None:
                    if action.termination_reason in {
                        TerminationReason.COMPLETED,
                        TerminationReason.COMPLETED_AFTER_REPAIR,
                    }:
                        if action.next_state is not WorkflowState.DONE:
                            raise InvalidTransitionError("completion requires transition to DONE")
                        validate_transition(current, action.next_state)
                        context.state = action.next_state
                        context.visited_states.append(action.next_state)
                    terminal_reason = action.termination_reason
                    error = action.error
                    break

                validate_transition(current, action.next_state)
                context.state = action.next_state
                context.visited_states.append(action.next_state)
                self._check_timeout(started)

            if terminal_reason is None:
                terminal_reason = (
                    TerminationReason.COMPLETED_AFTER_REPAIR
                    if WorkflowState.REPAIR in context.visited_states
                    else TerminationReason.COMPLETED
                )
        except Exception as exc:  # classification is part of this boundary
            terminal_reason = self._classify_exception(exc)
            error = str(exc)

        elapsed = monotonic() - started
        rolled_back = False
        if terminal_reason not in {
            TerminationReason.COMPLETED,
            TerminationReason.COMPLETED_AFTER_REPAIR,
        }:
            abort = getattr(self._driver, "abort", None)
            if callable(abort):
                rolled_back = bool(abort())
                if rolled_back:
                    context.edits.clear()
        metadata = dict(context.metadata)
        metadata.update(
            {
                "final_state": context.state.value,
                "phase_calls": {state.value: count for state, count in context.phase_calls.items()},
                "visited_states": [state.value for state in context.visited_states],
                "campaign_rollback_complete": rolled_back,
                "phase_tokens": phase_tokens,
                "successful_repairs": (
                    1 if terminal_reason is TerminationReason.COMPLETED_AFTER_REPAIR else 0
                ),
                "repair_attempts": int(context.metadata.get("repair_attempts", 0)),
            }
        )
        return AgentResult(
            edits=context.edits,
            termination_reason=terminal_reason,
            model_calls=context.model_calls,
            input_tokens=token_totals["input_tokens"],
            output_tokens=token_totals["output_tokens"],
            cache_read_tokens=token_totals["cache_read_tokens"],
            cache_write_tokens=token_totals["cache_write_tokens"],
            seconds=elapsed,
            error=error,
            plan=context.plan,
            tool_events=events,
            metadata=metadata,
        )

    def _account_calls(self, context: LoopContext, state: WorkflowState, model_calls: int) -> None:
        context.model_calls += model_calls
        context.phase_calls[state] = context.phase_calls.get(state, 0) + model_calls
        if context.model_calls > self._budgets.total_calls:
            raise _IterationLimitError("overall model-call budget exceeded")
        limit = self._budgets.limit_for(state)
        if limit is not None and context.phase_calls[state] > limit:
            raise _IterationLimitError(f"{state.value} model-call budget exceeded")

    def _check_call_budget(self, context: LoopContext) -> None:
        if context.model_calls >= self._budgets.total_calls:
            raise _IterationLimitError("overall model-call budget exceeded")
        limit = self._budgets.limit_for(context.state)
        if limit is not None and context.phase_calls.get(context.state, 0) >= limit:
            raise _IterationLimitError(f"{context.state.value} model-call budget exceeded")

    def _check_timeout(self, started: float) -> None:
        if monotonic() - started > self._budgets.timeout_seconds:
            raise AgentTimeoutError("agent timeout exceeded")

    @staticmethod
    def _classify_exception(exc: Exception) -> TerminationReason:
        if isinstance(exc, AgentTimeoutError | TimeoutError):
            return TerminationReason.AGENT_TIMEOUT
        if isinstance(exc, ProviderFailureError):
            return TerminationReason.PROVIDER_FAILURE
        if isinstance(exc, InvalidPatchError):
            return TerminationReason.INVALID_PATCH
        if isinstance(exc, CompletionAuditError):
            return TerminationReason.COMPLETION_AUDIT_FAILURE
        if isinstance(exc, GateFailureError):
            return TerminationReason.GATE_FAILURE
        if isinstance(exc, _IterationLimitError):
            return TerminationReason.ITERATION_LIMIT
        return TerminationReason.MALFORMED_RESPONSE


class _IterationLimitError(AgentLoopError):
    pass
