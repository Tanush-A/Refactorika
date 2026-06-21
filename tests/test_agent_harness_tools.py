from pathlib import Path

from eval.agents.harness_tools import (
    HarnessDeveloperTools,
    HarnessMutationExecutor,
    completion_audit,
)
from eval.agents.schema import PlanStep, RefactorPlan
from eval.agents.tools import DeveloperTools


def test_harness_mutation_rejects_escape_and_test_edits(tmp_path: Path) -> None:
    executor = HarnessMutationExecutor(tmp_path)

    escape = executor.submit_patch(
        {"edits": {"../outside.py": "x = 1\n"}, "refactor_kind": "rename"}
    )
    tests = executor.submit_patch(
        {"edits": {"tests/test_app.py": "def test_x(): pass\n"}, "refactor_kind": "rename"}
    )

    assert escape["error_class"] == "invalid_patch"
    assert tests["error_class"] == "invalid_patch"


def test_agentic_arms_have_identical_public_developer_tools(tmp_path: Path) -> None:
    control = DeveloperTools(tmp_path)
    harness = HarnessDeveloperTools(tmp_path)

    def public(value: object) -> set[str]:
        return {name for name in dir(value) if not name.startswith("_")}

    assert public(control) == public(harness)


def test_completion_audit_blocks_incomplete_campaign(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = 2\n")
    plan = RefactorPlan(
        objective="rename",
        rationale="clarity",
        affected_paths=["app.py"],
        expected_call_sites=[],
        compatibility_requirements=[],
        structural_postconditions=[],
        steps=[PlanStep("step-1", "rename", ["app.py"], completed=False)],
    )

    result = completion_audit(tmp_path, plan, {"app.py": "value = 1\n"})

    assert result["status"] == "failed"
    assert result["failures"] == ["incomplete plan steps: step-1"]
