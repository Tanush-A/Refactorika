"""Atomic execution of multi-step repository refactoring campaigns."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from eval.agents.schema import PlanStep, RefactorPlan
from eval.agents.tools import DeveloperTools, ToolResult


@dataclass(frozen=True)
class PatchPayload:
    """The common mutation representation used by both loop-agent arms."""

    edits: dict[str, str]
    refactor_kind: str
    plan_step: str


class PatchSubmitter(Protocol):
    def __call__(
        self,
        *,
        edits: dict[str, str],
        refactor_kind: str,
        plan_step: str,
    ) -> ToolResult: ...


VerificationCallback = Callable[[PlanStep], ToolResult]
CompletionAuditCallback = Callable[[RefactorPlan], ToolResult]


@dataclass(frozen=True)
class CampaignResult:
    status: str
    completed_steps: tuple[str, ...] = ()
    failed_step: str | None = None
    error: str | None = None
    error_class: str | None = None
    rollback_complete: bool = False
    audit: ToolResult | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class RefactorCampaign:
    """Apply verified plan steps as one repository-level transaction.

    The campaign snapshots all non-git files before the first mutation. Any
    submission failure, verification failure, incomplete plan, or completion
    audit failure restores that exact baseline, including removal of new files.
    """

    def __init__(
        self,
        *,
        tools: DeveloperTools,
        plan: RefactorPlan,
        submit: PatchSubmitter,
        verify_step: VerificationCallback,
        completion_audit: CompletionAuditCallback,
    ) -> None:
        self.tools = tools
        self.plan = plan
        self.submit = submit
        self.verify_step = verify_step
        self.completion_audit = completion_audit
        self._baseline = self._snapshot()
        self._completed: list[str] = []
        self._closed = False
        self._steps = {step.id: step for step in plan.steps}
        if len(self._steps) != len(plan.steps):
            raise ValueError("plan step ids must be unique")

    @property
    def completed_steps(self) -> tuple[str, ...]:
        return tuple(self._completed)

    def _files(self) -> list[Path]:
        return [
            path
            for path in self.tools.repo.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(self.tools.repo)
            and ".git" not in path.relative_to(self.tools.repo).parts
        ]

    def _snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.tools.repo).as_posix(): path.read_bytes()
            for path in self._files()
        }

    def _rollback(self) -> bool:
        try:
            current = {
                path.relative_to(self.tools.repo).as_posix(): path for path in self._files()
            }
            for relative, path in current.items():
                if relative not in self._baseline:
                    path.unlink()
            for relative, content in self._baseline.items():
                target = self.tools.repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for step in self.plan.steps:
                step.completed = False
            self._closed = True
            return self._snapshot() == self._baseline
        except OSError:
            self._closed = True
            return False

    def _failure(
        self,
        *,
        status: str,
        failed_step: str | None,
        result: ToolResult | None = None,
        error: str | None = None,
        error_class: str | None = None,
        audit: ToolResult | None = None,
    ) -> CampaignResult:
        rollback_complete = self._rollback()
        return CampaignResult(
            status=status,
            completed_steps=self.completed_steps,
            failed_step=failed_step,
            error=error if result is None else result.error,
            error_class=error_class if result is None else result.error_class,
            rollback_complete=rollback_complete,
            audit=audit,
        )

    def execute_step(self, payload: PatchPayload) -> CampaignResult:
        if self._closed:
            return CampaignResult(status="campaign_closed", error="campaign is closed")
        step = self._steps.get(payload.plan_step)
        if step is None:
            return self._failure(
                status="invalid_step",
                failed_step=payload.plan_step,
                error="patch references an unknown plan step",
                error_class="InvalidPlanStep",
            )
        if step.id in self._completed:
            return self._failure(
                status="invalid_step",
                failed_step=step.id,
                error="plan step was already completed",
                error_class="InvalidPlanStep",
            )
        missing = [
            dependency
            for dependency in step.dependencies
            if dependency not in self._completed
        ]
        if missing:
            return self._failure(
                status="invalid_transition",
                failed_step=step.id,
                error=f"uncompleted dependencies: {', '.join(missing)}",
                error_class="InvalidPlanTransition",
            )
        unexpected = sorted(set(payload.edits) - set(step.affected_paths))
        if unexpected:
            return self._failure(
                status="invalid_patch",
                failed_step=step.id,
                error=f"patch edits paths outside plan step: {', '.join(unexpected)}",
                error_class="InvalidPatch",
            )

        submitted = self.submit(
            edits=payload.edits,
            refactor_kind=payload.refactor_kind,
            plan_step=payload.plan_step,
        )
        if not submitted.ok:
            return self._failure(status="submission_failed", failed_step=step.id, result=submitted)
        verified = self.verify_step(step)
        if not verified.ok:
            return self._failure(status="verification_failed", failed_step=step.id, result=verified)
        step.completed = True
        self._completed.append(step.id)
        return CampaignResult(status="step_verified", completed_steps=self.completed_steps)

    def finish(self) -> CampaignResult:
        if self._closed:
            return CampaignResult(status="campaign_closed", error="campaign is closed")
        incomplete = [step.id for step in self.plan.steps if step.id not in self._completed]
        if incomplete:
            return self._failure(
                status="incomplete",
                failed_step=incomplete[0],
                error=f"incomplete plan steps: {', '.join(incomplete)}",
                error_class="IncompleteCampaign",
            )
        audit = self.completion_audit(self.plan)
        if not audit.ok:
            return self._failure(
                status="completion_audit_failed",
                failed_step=None,
                result=audit,
                audit=audit,
            )
        self._closed = True
        return CampaignResult(
            status="completed",
            completed_steps=self.completed_steps,
            audit=audit,
            metadata={"baseline_files": len(self._baseline)},
        )
