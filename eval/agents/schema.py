"""Machine-readable contracts shared by control and harness agent loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WorkflowState(StrEnum):
    DISCOVER = "discover"
    SELECT = "select"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    REPAIR = "repair"
    COMPLETION_AUDIT = "completion_audit"
    DONE = "done"


class TerminationReason(StrEnum):
    COMPLETED = "completed"
    COMPLETED_AFTER_REPAIR = "completed_after_repair"
    SAFE_ESCALATION = "safe_escalation"
    ITERATION_LIMIT = "iteration_limit"
    AGENT_TIMEOUT = "agent_timeout"
    PROVIDER_FAILURE = "provider_failure"
    GATE_FAILURE = "gate_failure"
    MALFORMED_RESPONSE = "malformed_response"
    INVALID_PATCH = "invalid_patch"
    COMPLETION_AUDIT_FAILURE = "completion_audit_failure"


@dataclass(frozen=True)
class Postcondition:
    kind: str
    path: str
    symbol: str | None = None
    detail: str | None = None


@dataclass
class PlanStep:
    id: str
    objective: str
    affected_paths: list[str]
    dependencies: list[str] = field(default_factory=list)
    verification_requirements: list[str] = field(default_factory=list)
    completed: bool = False


@dataclass
class RefactorPlan:
    objective: str
    rationale: str
    affected_paths: list[str]
    expected_call_sites: list[str]
    compatibility_requirements: list[str]
    structural_postconditions: list[Postcondition]
    steps: list[PlanStep]


@dataclass(frozen=True)
class ToolEvent:
    arm: str
    case: str
    trial: int
    sequence: int
    tool: str
    started_at: str
    seconds: float
    status: str
    error_class: str | None
    input_size: int
    output_size: int


@dataclass
class AgentResult:
    edits: dict[str, str]
    termination_reason: TerminationReason
    model_calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    seconds: float = 0.0
    error: str | None = None
    plan: RefactorPlan | None = None
    tool_events: list[ToolEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
