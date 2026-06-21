from pathlib import Path
from typing import Any

from eval.agents.driver import SharedAgentDriver, developer_tool_schemas
from eval.agents.harness_tools import HarnessContext, HarnessDeveloperTools
from eval.agents.loop import AgentLoop
from eval.agents.providers import ToolCompletion, Usage
from eval.agents.schema import TerminationReason, WorkflowState
from eval.agents.tools import DeveloperTools, ToolResult


class PassingGateTools(DeveloperTools):
    def run_tests(self, paths=()) -> ToolResult:
        del paths
        return ToolResult(status="ok")

    def run_lint(self) -> ToolResult:
        return ToolResult(status="ok")

    def run_typecheck(self) -> ToolResult:
        return ToolResult(status="ok")


class ScriptedProvider:
    def __init__(self, turns: list[list[dict[str, Any]]]) -> None:
        self.turns = iter(turns)
        self.schemas: list[list[dict[str, Any]]] = []

    def complete_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        tools: list[dict[str, Any]],
        arm: str,
        timeout: float | None = None,
    ) -> ToolCompletion:
        del messages, system, arm, timeout
        self.schemas.append(tools)
        return ToolCompletion(next(self.turns), Usage(input_tokens=10, output_tokens=2), 0.01)


def use(name: str, arguments: dict[str, Any], number: int) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"tool-{number}", "name": name, "input": arguments}


def plan() -> dict[str, Any]:
    return {
        "objective": "rename helper",
        "rationale": "clarify intent",
        "affected_paths": ["app.py"],
        "expected_call_sites": [],
        "compatibility_requirements": ["preserve behavior"],
        "structural_postconditions": [
            {"kind": "defines", "path": "app.py", "symbol": "format_name"}
        ],
        "steps": [
            {
                "id": "step-1",
                "objective": "rename helper",
                "affected_paths": ["app.py"],
                "verification_requirements": ["tests"],
            }
        ],
    }


def test_shared_driver_runs_tools_patch_and_structured_workflow(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def old():\n    return 1\n")
    provider = ScriptedProvider(
        [
            [use("list_files", {"pattern": "*.py"}, 1)],
            [use("workflow_action", {"next_state": "select"}, 2)],
            [use("workflow_action", {"next_state": "execute", "plan": plan()}, 3)],
            [
                use(
                    "submit_patch",
                    {
                        "edits": {"app.py": "def format_name():\n    return 1\n"},
                        "refactor_kind": "rename",
                        "plan_step": "step-1",
                    },
                    4,
                )
            ],
        ]
    )
    driver = SharedAgentDriver(
        provider,
        PassingGateTools(tmp_path),
        arm="agentic",
        case="rename",
        trial=1,
        user_prompt="refactor this codebase",
    )

    result = AgentLoop(driver).run()

    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.model_calls == 4
    assert result.input_tokens == 40
    assert result.plan is not None and result.plan.steps[0].id == "step-1"
    assert result.edits["app.py"].startswith("def format_name")
    assert [event.tool for event in result.tool_events] == [
        "list_files",
        "workflow_action",
        "workflow_action",
        "submit_patch",
        "run_tests",
        "run_lint",
        "run_typecheck",
        "completion_audit",
    ]
    assert [event.sequence for event in result.tool_events] == list(range(1, 9))
    assert result.plan.steps[0].completed is True
    assert result.metadata["completion_audit"]["status"] == "passed"
    assert result.metadata["visited_states"] == [
        "discover",
        "discover",
        "select",
        "plan",
        "execute",
        "verify",
        "completion_audit",
        "done",
    ]


def test_tool_contract_contains_standard_patch_shape_and_is_defensive() -> None:
    first = developer_tool_schemas()
    second = developer_tool_schemas()
    patch = next(tool for tool in first if tool["name"] == "submit_patch")

    assert patch["input_schema"]["required"] == ["edits", "refactor_kind", "plan_step"]
    assert set(patch["input_schema"]["properties"]) == {
        "edits",
        "refactor_kind",
        "plan_step",
    }
    first[0]["name"] = "mutated"
    assert second[0]["name"] == "list_files"


def test_harness_context_is_injected_without_changing_tool_schemas(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    control = SharedAgentDriver(
        ScriptedProvider([]),
        DeveloperTools(tmp_path),
        arm="agentic",
        case="case",
        trial=0,
        user_prompt="refactor this codebase",
    )
    # Supplying bounded precomputed context exercises injection without invoking a repo audit.
    harness_shaped = SharedAgentDriver(
        ScriptedProvider([]),
        HarnessDeveloperTools(tmp_path),
        arm="agentic+harness",
        case="case",
        trial=0,
        user_prompt="refactor this codebase",
        harness_context={"audit": {"entries": []}, "dependency_plan": {"tasks": []}},
    )

    assert control.tool_schemas == harness_shaped.tool_schemas
    assert "harness_context" not in control.messages[0]["content"]
    assert "harness_context" in harness_shaped.messages[0]["content"]


def test_harness_bootstrap_runs_automatically_before_first_call(tmp_path: Path) -> None:
    calls: list[Path] = []

    def bootstrap(repo: Path) -> HarnessContext:
        calls.append(repo)
        return HarnessContext(
            audit={"entries": []},
            dependency_plan={"tasks": []},
            architecture_notes={},
            remembered_context={},
        )

    driver = SharedAgentDriver(
        ScriptedProvider([]),
        HarnessDeveloperTools(tmp_path),
        arm="agentic+harness",
        case="case",
        trial=0,
        user_prompt="refactor this codebase",
        bootstrapper=bootstrap,
    )

    assert calls == [tmp_path.resolve()]
    assert "harness_context" in driver.messages[0]["content"]


def test_provider_failure_maps_through_shared_loop(tmp_path: Path) -> None:
    class FailedProvider(ScriptedProvider):
        def complete_tools(self, *args: Any, **kwargs: Any) -> ToolCompletion:
            return ToolCompletion([], Usage(), 1.0, "rate limited", "provider_failure")

    result = AgentLoop(
        SharedAgentDriver(
            FailedProvider([]),
            DeveloperTools(tmp_path),
            arm="agentic",
            case="case",
            trial=0,
            user_prompt="refactor this codebase",
        )
    ).run()

    assert result.termination_reason is TerminationReason.PROVIDER_FAILURE
    assert result.error == "rate limited"


def test_verify_rejects_workflow_without_patch(tmp_path: Path) -> None:
    provider = ScriptedProvider([])
    driver = SharedAgentDriver(
        provider,
        DeveloperTools(tmp_path),
        arm="agentic",
        case="case",
        trial=0,
        user_prompt="refactor this codebase",
    )

    action = AgentLoop(lambda _state, _context: driver(WorkflowState.VERIFY, _context)).run()

    assert action.termination_reason is TerminationReason.MALFORMED_RESPONSE


def test_model_cannot_stop_before_execution_and_completion_audit(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            [use("workflow_action", {"next_state": "select"}, 1)],
            [use("workflow_action", {"next_state": "execute", "plan": plan()}, 2)],
            [use("workflow_action", {"next_state": "done"}, 3)],
        ]
    )
    result = AgentLoop(
        SharedAgentDriver(
            provider,
            PassingGateTools(tmp_path),
            arm="agentic",
            case="early-stop",
            trial=0,
            user_prompt="refactor this codebase",
        )
    ).run()

    assert result.termination_reason is TerminationReason.MALFORMED_RESPONSE
    assert result.metadata["final_state"] == "execute"


def test_multi_step_plan_must_finish_before_completion_audit(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("A = 1\n")
    (tmp_path / "caller.py").write_text("from app import A\n")
    multi_plan = plan()
    multi_plan["affected_paths"] = ["app.py", "caller.py"]
    multi_plan["steps"] = [
        {"id": "one", "objective": "rename", "affected_paths": ["app.py"]},
        {
            "id": "two",
            "objective": "update caller",
            "affected_paths": ["caller.py"],
            "dependencies": ["one"],
        },
    ]
    provider = ScriptedProvider(
        [
            [use("workflow_action", {"next_state": "select"}, 1)],
            [use("workflow_action", {"next_state": "execute", "plan": multi_plan}, 2)],
            [
                use(
                    "submit_patch",
                    {
                        "edits": {"app.py": "RENAMED = 1\n"},
                        "refactor_kind": "rename",
                        "plan_step": "one",
                    },
                    3,
                )
            ],
            [
                use(
                    "submit_patch",
                    {
                        "edits": {"caller.py": "from app import RENAMED\n"},
                        "refactor_kind": "rename",
                        "plan_step": "two",
                    },
                    4,
                )
            ],
        ]
    )

    result = AgentLoop(
        SharedAgentDriver(
            provider,
            PassingGateTools(tmp_path),
            arm="agentic",
            case="multi-step",
            trial=0,
            user_prompt="refactor this codebase",
        )
    ).run()

    assert result.termination_reason is TerminationReason.COMPLETED
    assert [step.completed for step in result.plan.steps] == [True, True]
    assert result.metadata["visited_states"].count("execute") == 2
    assert result.metadata["completion_audit"]["completed_steps"] == ["one", "two"]


class RepairGateTools(PassingGateTools):
    def __init__(self, repo: Path, *, always_fail: bool = False) -> None:
        super().__init__(repo)
        self.test_calls = 0
        self.always_fail = always_fail

    def run_tests(self, paths=()) -> ToolResult:
        del paths
        self.test_calls += 1
        if self.always_fail or self.test_calls == 1:
            return ToolResult(
                status="error",
                error="tests failed",
                error_class="CommandFailure",
            )
        return ToolResult(status="ok")


def _repair_turns() -> list[list[dict[str, Any]]]:
    patch = {
        "edits": {"app.py": "def format_name():\n    return 1\n"},
        "refactor_kind": "rename",
        "plan_step": "step-1",
    }
    return [
        [use("workflow_action", {"next_state": "select"}, 1)],
        [use("workflow_action", {"next_state": "execute", "plan": plan()}, 2)],
        [use("submit_patch", patch, 3)],
        [use("submit_patch", patch, 4)],
    ]


def test_completion_audit_allows_one_successful_repair(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def old():\n    return 1\n")
    tools = RepairGateTools(tmp_path)
    result = AgentLoop(
        SharedAgentDriver(
            ScriptedProvider(_repair_turns()),
            tools,
            arm="agentic",
            case="audit-repair",
            trial=0,
            user_prompt="refactor this codebase",
        )
    ).run()

    assert result.termination_reason is TerminationReason.COMPLETED_AFTER_REPAIR
    assert tools.test_calls == 2
    assert result.metadata["completion_audit"]["status"] == "passed"
    assert result.metadata["visited_states"].count("repair") == 1


def test_second_completion_gate_failure_terminates_without_more_repairs(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("def old():\n    return 1\n")
    tools = RepairGateTools(tmp_path, always_fail=True)
    provider = ScriptedProvider(_repair_turns())
    result = AgentLoop(
        SharedAgentDriver(
            provider,
            tools,
            arm="agentic",
            case="audit-failure",
            trial=0,
            user_prompt="refactor this codebase",
        )
    ).run()

    assert result.termination_reason is TerminationReason.COMPLETION_AUDIT_FAILURE
    assert tools.test_calls == 2
    assert result.model_calls == 4
    assert result.metadata["completion_audit"]["status"] == "failed"
    assert result.metadata["campaign_rollback_complete"] is True
    assert result.edits == {}
    assert (tmp_path / "app.py").read_text().startswith("def old")
