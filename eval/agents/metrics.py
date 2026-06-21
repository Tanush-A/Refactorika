"""Observability projections for loop-agent benchmark results."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from eval.agents.schema import AgentResult, ToolEvent

GATE_TOOLS = frozenset(
    {
        "run_tests",
        "run_lint",
        "run_typecheck",
        "apply_and_verify",
        "apply_and_verify_multi",
        "completion_audit",
    }
)
EDIT_TOOLS = frozenset({"submit_patch", "apply_and_verify", "apply_and_verify_multi"})


def _optional_int(metadata: Mapping[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(metadata: Mapping[str, Any], key: str) -> bool | None:
    value = metadata.get(key)
    return value if isinstance(value, bool) else None


def _phase_tokens(metadata: Mapping[str, Any]) -> dict[str, int] | None:
    value = metadata.get("phase_tokens")
    if not isinstance(value, Mapping):
        return None
    parsed = {
        str(phase): count
        for phase, count in value.items()
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
    }
    return parsed or None


def _first_event_sequence(events: Iterable[ToolEvent], tools: frozenset[str]) -> int | None:
    sequences = [event.sequence for event in events if event.tool in tools]
    return min(sequences) if sequences else None


@dataclass(frozen=True)
class AgentMetrics:
    termination_reason: str
    model_calls: int
    calls_before_plan: int | None
    calls_before_first_edit: int | None
    first_edit_event_sequence: int | None
    tool_counts: dict[str, int]
    tool_error_counts: dict[str, int]
    gate_calls: int
    rollbacks: int | None
    repair_attempts: int | None
    successful_repairs: int | None
    repair_effectiveness: float | None
    completion_audit_failures: int | None
    plan_steps_total: int | None
    plan_steps_completed: int | None
    plan_step_completion_rate: float | None
    tokens_by_phase: dict[str, int] | None
    input_tokens: int
    output_tokens: int
    final_diff_lines: int | None
    final_diff_bytes: int | None
    final_diff_files: int | None
    sequential_fallback: bool | None
    event_count: int
    uninstrumented_fields: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_agent_metrics(result: AgentResult) -> AgentMetrics:
    """Project one agent result into benchmark-ready operational metrics."""

    metadata = result.metadata
    events = result.tool_events
    counts = Counter(event.tool for event in events)
    errors = Counter(
        event.tool
        for event in events
        if event.error_class is not None
        or event.status in {"error", "failed", "timeout", "rolled_back", "rolled-back"}
    )
    gate_calls = sum(counts[tool] for tool in GATE_TOOLS)
    rollbacks = _optional_int(metadata, "rollbacks")
    if rollbacks is None:
        observed_rollbacks = sum(
            event.tool == "rollback"
            or event.status in {"rollback", "rolled_back", "rolled-back"}
            for event in events
        )
        rollbacks = observed_rollbacks or None
    audit_failures = _optional_int(metadata, "completion_audit_failures")
    if audit_failures is None:
        observed_audit_failures = sum(
            event.tool == "completion_audit"
            and (
                event.error_class is not None
                or event.status in {"error", "failed", "timeout", "rolled_back", "rolled-back"}
            )
            for event in events
        )
        audit_failures = observed_audit_failures or None

    repair_attempts = _optional_int(metadata, "repair_attempts")
    successful_repairs = _optional_int(metadata, "successful_repairs")
    repair_effectiveness = (
        successful_repairs / repair_attempts
        if repair_attempts is not None
        and repair_attempts > 0
        and successful_repairs is not None
        else None
    )

    total_steps: int | None = len(result.plan.steps) if result.plan is not None else None
    completed_steps: int | None = (
        sum(step.completed for step in result.plan.steps) if result.plan is not None else None
    )
    completion_rate = (
        completed_steps / total_steps
        if total_steps is not None and total_steps > 0 and completed_steps is not None
        else None
    )

    values: dict[str, Any] = {
        "calls_before_plan": _optional_int(metadata, "calls_before_plan"),
        "calls_before_first_edit": _optional_int(metadata, "calls_before_first_edit"),
        "rollbacks": rollbacks,
        "repair_attempts": repair_attempts,
        "successful_repairs": successful_repairs,
        "completion_audit_failures": audit_failures,
        "tokens_by_phase": _phase_tokens(metadata),
        "final_diff_lines": _optional_int(metadata, "final_diff_lines"),
        "final_diff_bytes": _optional_int(metadata, "final_diff_bytes"),
        "final_diff_files": _optional_int(metadata, "final_diff_files"),
        "sequential_fallback": _optional_bool(metadata, "sequential_fallback"),
    }
    missing = tuple(sorted(key for key, value in values.items() if value is None))
    return AgentMetrics(
        termination_reason=result.termination_reason.value,
        model_calls=result.model_calls,
        calls_before_plan=values["calls_before_plan"],
        calls_before_first_edit=values["calls_before_first_edit"],
        first_edit_event_sequence=_first_event_sequence(events, EDIT_TOOLS),
        tool_counts=dict(sorted(counts.items())),
        tool_error_counts=dict(sorted(errors.items())),
        gate_calls=gate_calls,
        rollbacks=values["rollbacks"],
        repair_attempts=repair_attempts,
        successful_repairs=successful_repairs,
        repair_effectiveness=repair_effectiveness,
        completion_audit_failures=values["completion_audit_failures"],
        plan_steps_total=total_steps,
        plan_steps_completed=completed_steps,
        plan_step_completion_rate=completion_rate,
        tokens_by_phase=values["tokens_by_phase"],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        final_diff_lines=values["final_diff_lines"],
        final_diff_bytes=values["final_diff_bytes"],
        final_diff_files=values["final_diff_files"],
        sequential_fallback=values["sequential_fallback"],
        event_count=len(events),
        uninstrumented_fields=missing,
    )


def aggregate_agent_metrics(results: Iterable[AgentResult]) -> dict[str, Any]:
    """Aggregate runs without discarding their per-run metric records."""

    records = [collect_agent_metrics(result) for result in results]
    termination_reasons = Counter(record.termination_reason for record in records)
    tool_counts: Counter[str] = Counter()
    for record in records:
        tool_counts.update(record.tool_counts)
    return {
        "runs": len(records),
        "records": [record.to_dict() for record in records],
        "termination_reasons": dict(sorted(termination_reasons.items())),
        "tool_counts": dict(sorted(tool_counts.items())),
        "model_calls": sum(record.model_calls for record in records),
        "input_tokens": sum(record.input_tokens for record in records),
        "output_tokens": sum(record.output_tokens for record in records),
        "gate_calls": sum(record.gate_calls for record in records),
        "event_count": sum(record.event_count for record in records),
    }
