"""Shared model/tool driver for the two agentic benchmark arms."""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from eval.agents.harness_tools import (
    HarnessContext,
    HarnessDeveloperTools,
    bootstrap_harness_context,
)
from eval.agents.loop import (
    LoopAction,
    LoopContext,
    MalformedResponseError,
    ProviderFailureError,
)
from eval.agents.prompts import AGENTIC_HARNESS_SYSTEM, AGENTIC_SYSTEM
from eval.agents.providers import ToolCompletion
from eval.agents.schema import (
    PlanStep,
    Postcondition,
    RefactorPlan,
    TerminationReason,
    ToolEvent,
    WorkflowState,
)
from eval.agents.tools import DeveloperTools, ToolResult


class ToolProvider(Protocol):
    """Provider boundary implemented by :class:`HttpProvider`."""

    def complete_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        tools: list[dict[str, Any]],
        arm: str,
        timeout: float | None = None,
    ) -> ToolCompletion: ...


_PATCH_PROPERTIES = {
    "edits": {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "description": "Complete new content keyed by repository-relative path.",
    },
    "refactor_kind": {"type": "string"},
    "plan_step": {"type": "string"},
}


def developer_tool_schemas() -> list[dict[str, Any]]:
    """Return the identical model-visible tool contract used by both loop arms."""

    return [
        _tool("list_files", "List repository files.", {"pattern": {"type": "string"}}),
        _tool(
            "glob_files",
            "List files matching a glob.",
            {"pattern": {"type": "string"}},
            ("pattern",),
        ),
        _tool(
            "read_file",
            "Read a bounded line range from one file.",
            {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            ("path",),
        ),
        _tool(
            "read_files",
            "Read a bounded batch of files.",
            {"paths": {"type": "array", "items": {"type": "string"}}},
            ("paths",),
        ),
        _tool(
            "search_code",
            "Search repository text with line numbers.",
            {"query": {"type": "string"}, "glob": {"type": "string"}},
            ("query",),
        ),
        _tool(
            "find_references",
            "Find textual references to a Python symbol.",
            {"symbol": {"type": "string"}},
            ("symbol",),
        ),
        _tool("git_status", "Show concise repository status.", {}),
        _tool(
            "git_diff",
            "Show the current repository diff.",
            {"staged": {"type": "boolean"}},
        ),
        _tool(
            "run_tests",
            "Run repository tests, optionally narrowed to paths.",
            {"paths": {"type": "array", "items": {"type": "string"}}},
        ),
        _tool("run_lint", "Run the configured linter.", {}),
        _tool("run_typecheck", "Run the configured type checker.", {}),
        _tool(
            "submit_patch",
            "Atomically submit complete contents for one or more source files.",
            _PATCH_PROPERTIES,
            ("edits", "refactor_kind", "plan_step"),
        ),
        _tool(
            "workflow_action",
            "Declare the next workflow state and, during planning, the structured plan.",
            {
                "next_state": {
                    "type": "string",
                    "enum": [
                        state.value
                        for state in WorkflowState
                        if state is not WorkflowState.DISCOVER
                    ],
                },
                "plan": {"type": "object"},
                "termination_reason": {
                    "type": "string",
                    "enum": [reason.value for reason in TerminationReason],
                },
                "error": {"type": "string"},
            },
            ("next_state",),
        ),
    ]


def _tool(
    name: str,
    description: str,
    properties: Mapping[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": dict(properties),
            "required": list(required),
            "additionalProperties": False,
        },
    }


Bootstrapper = Callable[..., HarnessContext]
_UNSET = object()


class SharedAgentDriver:
    """Decode model tool turns and execute them against a shared tool interface."""

    def __init__(
        self,
        provider: ToolProvider,
        tools: DeveloperTools,
        *,
        arm: str,
        case: str,
        trial: int,
        user_prompt: str,
        timeout: float = 180.0,
        harness_context: HarnessContext | Mapping[str, Any] | None | object = _UNSET,
        bootstrapper: Bootstrapper = bootstrap_harness_context,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.arm = arm
        self.case = case
        self.trial = trial
        self.timeout = timeout
        self._sequence = 0
        self._last_patch_ok: bool | None = None
        self._plan: RefactorPlan | None = None
        self._completion_repair_attempted = False
        self._baseline = self._source_snapshot()
        self._schemas = developer_tool_schemas()
        is_harness = isinstance(tools, HarnessDeveloperTools)
        if is_harness and harness_context is _UNSET:
            harness_context = bootstrapper(tools.repo)
        self.system = AGENTIC_HARNESS_SYSTEM if is_harness else AGENTIC_SYSTEM
        initial: dict[str, Any] = {"user_prompt": user_prompt}
        if harness_context is not _UNSET and harness_context is not None:
            initial["harness_context"] = (
                asdict(harness_context)
                if isinstance(harness_context, HarnessContext)
                else dict(cast(Mapping[str, Any], harness_context))
            )
        self.messages: list[dict[str, Any]] = [
            {"role": "user", "content": json.dumps(initial, sort_keys=True)}
        ]

    @property
    def tool_schemas(self) -> list[dict[str, Any]]:
        """Return a defensive copy so callers cannot create arm schema drift."""

        return cast(list[dict[str, Any]], json.loads(json.dumps(self._schemas)))

    def __call__(self, state: WorkflowState, context: LoopContext) -> LoopAction:
        if state is WorkflowState.SELECT:
            return LoopAction(WorkflowState.PLAN)
        if state is WorkflowState.VERIFY:
            if self._last_patch_ok is None:
                raise MalformedResponseError("VERIFY reached before submit_patch")
            if (
                self._last_patch_ok
                and self._plan is not None
                and any(not step.completed for step in self._plan.steps)
            ):
                return LoopAction(WorkflowState.EXECUTE)
            return LoopAction(
                WorkflowState.COMPLETION_AUDIT if self._last_patch_ok else WorkflowState.REPAIR
            )
        if state is WorkflowState.COMPLETION_AUDIT:
            return self._run_completion_audit(context)

        completion = self.provider.complete_tools(
            self.messages,
            system=self.system,
            tools=self.tool_schemas,
            arm=self.arm,
            timeout=self.timeout,
        )
        if completion.error:
            if completion.error_class == "malformed_response":
                raise MalformedResponseError(completion.error)
            raise ProviderFailureError(completion.error)
        if not isinstance(completion.content, list):
            raise MalformedResponseError("provider content must be a list")

        self.messages.append({"role": "assistant", "content": completion.content})
        events: list[ToolEvent] = []
        tool_results: list[dict[str, Any]] = []
        declaration: dict[str, Any] | None = None
        submitted_edits: dict[str, str] = {}
        for block in completion.content:
            if not isinstance(block, dict):
                raise MalformedResponseError("provider content block must be an object")
            if block.get("type") != "tool_use":
                continue
            tool_id = block.get("id")
            name = block.get("name")
            arguments = block.get("input")
            if (
                not isinstance(tool_id, str)
                or not isinstance(name, str)
                or not isinstance(arguments, dict)
            ):
                raise MalformedResponseError("malformed tool_use block")
            if name == "workflow_action":
                if declaration is not None:
                    raise MalformedResponseError("multiple workflow_action declarations")
                declaration = arguments
                result = ToolResult(status="ok", data={"accepted": True})
            else:
                plan_step = self._validate_plan_step(arguments) if name == "submit_patch" else None
                result = self._invoke(name, arguments)
                if name == "submit_patch":
                    self._last_patch_ok = result.ok
                    if result.ok:
                        submitted_edits.update(cast(dict[str, str], arguments["edits"]))
                        if plan_step is None:
                            raise MalformedResponseError("submit_patch has no validated plan step")
                        plan_step.completed = True
            rendered = _render_result(result)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": rendered,
                    "is_error": not result.ok,
                }
            )
            events.append(self._event(name, arguments, rendered, result))
        if tool_results:
            self.messages.append({"role": "user", "content": tool_results})

        next_state, plan, reason, error = self._decode_action(
            state, declaration, submitted=bool(submitted_edits)
        )
        if plan is not None:
            self._plan = plan
        usage = completion.usage
        return LoopAction(
            next_state=next_state,
            model_calls=1,
            edits=submitted_edits,
            plan=plan,
            tool_events=events,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            termination_reason=reason,
            error=error,
            metadata={"provider_seconds": completion.seconds},
        )

    def _validate_plan_step(self, arguments: dict[str, Any]) -> PlanStep:
        if self._plan is None:
            raise MalformedResponseError("submit_patch requires an active plan")
        step_id = arguments.get("plan_step")
        if not isinstance(step_id, str):
            raise MalformedResponseError("submit_patch plan_step must be a string")
        step = next((candidate for candidate in self._plan.steps if candidate.id == step_id), None)
        if step is None:
            raise MalformedResponseError(f"submit_patch references unknown plan step: {step_id}")
        missing = [
            dependency
            for dependency in step.dependencies
            if not any(item.id == dependency and item.completed for item in self._plan.steps)
        ]
        if missing:
            raise MalformedResponseError(
                f"plan step {step_id} has incomplete dependencies: {', '.join(missing)}"
            )
        edits = arguments.get("edits")
        if not isinstance(edits, dict):
            raise MalformedResponseError("submit_patch edits must be an object")
        unexpected = sorted(set(edits) - set(step.affected_paths))
        if unexpected:
            raise MalformedResponseError(
                f"plan step {step_id} edits paths outside its scope: {', '.join(unexpected)}"
            )
        return step

    def _run_completion_audit(self, context: LoopContext) -> LoopAction:
        plan = self._plan or context.plan
        failures: list[str] = []
        if plan is None:
            failures.append("no active refactor plan")
        else:
            incomplete = [step.id for step in plan.steps if not step.completed]
            if incomplete:
                failures.append(f"incomplete plan steps: {', '.join(incomplete)}")
            changed = self._changed_source_paths()
            required = set(plan.affected_paths)
            missing = sorted(required - changed)
            outside_scope = sorted(changed - required - self._changed_test_paths())
            forbidden = sorted(self._changed_test_paths())
            if missing:
                failures.append(f"planned paths unchanged: {', '.join(missing)}")
            if outside_scope:
                failures.append(f"diff outside selected scope: {', '.join(outside_scope)}")
            if forbidden:
                failures.append(f"forbidden test changes: {', '.join(forbidden)}")

        events: list[ToolEvent] = []
        gate_results: dict[str, dict[str, Any]] = {}
        for name, method in (
            ("run_tests", self.tools.run_tests),
            ("run_lint", self.tools.run_lint),
            ("run_typecheck", self.tools.run_typecheck),
        ):
            result = method()
            rendered = _render_result(result)
            events.append(self._event(name, {}, rendered, result))
            gate_results[name] = {
                "status": result.status,
                "error": result.error,
                "error_class": result.error_class,
            }
            if not result.ok:
                failures.append(f"{name} failed: {result.error or result.error_class or 'unknown'}")

        audit_data = {
            "status": "passed" if not failures else "failed",
            "failures": failures,
            "changed_paths": sorted(self._changed_source_paths()),
            "completed_steps": (
                [step.id for step in plan.steps if step.completed] if plan is not None else []
            ),
            "gates": gate_results,
        }
        audit_result = ToolResult(
            status="ok" if not failures else "error",
            data=audit_data,
            error=None if not failures else "; ".join(failures),
            error_class=None if not failures else "CompletionAuditFailure",
        )
        rendered_audit = _render_result(audit_result)
        events.append(self._event("completion_audit", {}, rendered_audit, audit_result))
        metadata = {
            "completion_audit": audit_data,
            "completion_audit_failures": int(bool(failures)),
            **self._diff_metrics(),
        }
        if not failures:
            return LoopAction(WorkflowState.DONE, tool_events=events, metadata=metadata)
        if self._completion_repair_attempted:
            return LoopAction(
                WorkflowState.COMPLETION_AUDIT,
                tool_events=events,
                termination_reason=TerminationReason.COMPLETION_AUDIT_FAILURE,
                error="; ".join(failures),
                metadata=metadata,
            )
        self._completion_repair_attempted = True
        self._append_audit_feedback(audit_data)
        return LoopAction(WorkflowState.REPAIR, tool_events=events, metadata=metadata)

    def _append_audit_feedback(self, audit: dict[str, Any]) -> None:
        text = "Completion audit rejected the campaign. Repair only these failures: " + json.dumps(
            audit, sort_keys=True
        )
        if (
            self.messages
            and self.messages[-1].get("role") == "user"
            and isinstance(self.messages[-1].get("content"), list)
        ):
            cast(list[dict[str, Any]], self.messages[-1]["content"]).append(
                {"type": "text", "text": text}
            )
        else:
            self.messages.append({"role": "user", "content": text})

    def _source_snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.tools.repo).as_posix(): path.read_text(errors="replace")
            for path in self.tools.repo.rglob("*.py")
            if ".git" not in path.parts
            and ".venv" not in path.parts
            and "__pycache__" not in path.parts
        }

    def _diff_metrics(self) -> dict[str, int]:
        current = self._source_snapshot()
        changed = sorted(
            path
            for path in set(self._baseline) | set(current)
            if self._baseline.get(path) != current.get(path)
        )
        diff = "".join(
            "".join(
                difflib.unified_diff(
                    self._baseline.get(path, "").splitlines(keepends=True),
                    current.get(path, "").splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
            for path in changed
        )
        return {
            "final_diff_files": len(changed),
            "final_diff_lines": len(diff.splitlines()),
            "final_diff_bytes": len(diff.encode()),
        }

    def _changed_source_paths(self) -> set[str]:
        current = self._source_snapshot()
        return {
            path
            for path in set(self._baseline) | set(current)
            if self._baseline.get(path) != current.get(path)
        }

    def _changed_test_paths(self) -> set[str]:
        return {path for path in self._changed_source_paths() if self.tools._is_test_path(path)}

    def abort(self) -> bool:
        """Restore the source baseline when a campaign does not complete safely."""

        try:
            current = self._source_snapshot()
            for relative in set(current) - set(self._baseline):
                (self.tools.repo / relative).unlink(missing_ok=True)
            for relative, content in self._baseline.items():
                target = self.tools.repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            if self._plan is not None:
                for step in self._plan.steps:
                    step.completed = False
            return self._source_snapshot() == self._baseline
        except OSError:
            return False

    def _decode_action(
        self,
        state: WorkflowState,
        declaration: dict[str, Any] | None,
        *,
        submitted: bool,
    ) -> tuple[WorkflowState, RefactorPlan | None, TerminationReason | None, str | None]:
        if declaration is None:
            if submitted and state in {WorkflowState.EXECUTE, WorkflowState.REPAIR}:
                return WorkflowState.VERIFY, None, None, None
            if state in {WorkflowState.DISCOVER, WorkflowState.EXECUTE}:
                return state, None, None, None
            raise MalformedResponseError(f"{state.value} requires workflow_action")
        try:
            next_state = WorkflowState(str(declaration["next_state"]))
            reason_raw = declaration.get("termination_reason")
            reason = TerminationReason(str(reason_raw)) if reason_raw is not None else None
            plan = _decode_plan(declaration["plan"]) if "plan" in declaration else None
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedResponseError(f"invalid workflow_action: {exc}") from exc
        if state is WorkflowState.PLAN and plan is None:
            raise MalformedResponseError("planning transition requires a structured plan")
        return next_state, plan, reason, _optional_string(declaration.get("error"))

    def _invoke(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        methods: dict[str, Callable[..., ToolResult]] = {
            "list_files": self.tools.list_files,
            "glob_files": self.tools.glob_files,
            "read_file": self.tools.read_file,
            "read_files": self.tools.read_files,
            "search_code": self.tools.search_code,
            "find_references": self.tools.find_references,
            "git_status": self.tools.git_status,
            "git_diff": self.tools.git_diff,
            "run_tests": self.tools.run_tests,
            "run_lint": self.tools.run_lint,
            "run_typecheck": self.tools.run_typecheck,
            "submit_patch": self.tools.submit_patch,
        }
        method = methods.get(name)
        if method is None:
            return ToolResult(
                status="error",
                error=f"unknown tool: {name}",
                error_class="UnknownTool",
            )
        try:
            return method(**arguments)
        except (TypeError, ValueError) as exc:
            return ToolResult(status="error", error=str(exc), error_class=type(exc).__name__)

    def _event(
        self,
        name: str,
        arguments: dict[str, Any],
        rendered: str,
        result: ToolResult,
    ) -> ToolEvent:
        self._sequence += 1
        return ToolEvent(
            arm=self.arm,
            case=self.case,
            trial=self.trial,
            sequence=self._sequence,
            tool=name,
            started_at=(datetime.now(UTC) - timedelta(seconds=result.seconds)).isoformat(),
            seconds=result.seconds,
            status=result.status,
            error_class=result.error_class,
            input_size=len(json.dumps(arguments, sort_keys=True).encode()),
            output_size=len(rendered.encode()),
        )


def _render_result(result: ToolResult) -> str:
    return json.dumps(
        {
            "status": result.status,
            "data": result.data,
            "error": result.error,
            "error_class": result.error_class,
            "truncated": result.truncated,
            "metadata": result.metadata,
        },
        default=str,
        sort_keys=True,
    )


def _decode_plan(raw: Any) -> RefactorPlan:
    if not isinstance(raw, dict):
        raise TypeError("plan must be an object")
    return RefactorPlan(
        objective=_required_string(raw, "objective"),
        rationale=_required_string(raw, "rationale"),
        affected_paths=_string_list(raw, "affected_paths"),
        expected_call_sites=_string_list(raw, "expected_call_sites"),
        compatibility_requirements=_string_list(raw, "compatibility_requirements"),
        structural_postconditions=[
            _decode_postcondition(item) for item in _list(raw, "structural_postconditions")
        ],
        steps=[_decode_step(item) for item in _list(raw, "steps")],
    )


def _decode_postcondition(raw: Any) -> Postcondition:
    if not isinstance(raw, dict):
        raise TypeError("postcondition must be an object")
    return Postcondition(
        kind=_required_string(raw, "kind"),
        path=_required_string(raw, "path"),
        symbol=_optional_string(raw.get("symbol")),
        detail=_optional_string(raw.get("detail")),
    )


def _decode_step(raw: Any) -> PlanStep:
    if not isinstance(raw, dict):
        raise TypeError("plan step must be an object")
    return PlanStep(
        id=_required_string(raw, "id"),
        objective=_required_string(raw, "objective"),
        affected_paths=_string_list(raw, "affected_paths"),
        dependencies=_string_list(raw, "dependencies", default=[]),
        verification_requirements=_string_list(raw, "verification_requirements", default=[]),
        completed=raw.get("completed", False) is True,
    )


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional value must be a string")
    return value


def _list(raw: Mapping[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return value


def _string_list(
    raw: Mapping[str, Any], key: str, *, default: list[str] | None = None
) -> list[str]:
    value = raw.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{key} must be a string list")
    return value
