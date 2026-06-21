from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from eval.agents.campaign import PatchPayload, RefactorCampaign
from eval.agents.schema import PlanStep, RefactorPlan
from eval.agents.tools import DeveloperTools, ToolResult


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/a.py").write_text("VALUE = 1\n")
    (tmp_path / "src/b.py").write_text("from .a import VALUE\n")
    (tmp_path / "tests/test_a.py").write_text("def test_value(): pass\n")
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    return tmp_path


def make_plan() -> RefactorPlan:
    return RefactorPlan(
        objective="rename value",
        rationale="clearer name",
        affected_paths=["src/a.py", "src/b.py"],
        expected_call_sites=["src/b.py"],
        compatibility_requirements=[],
        structural_postconditions=[],
        steps=[
            PlanStep(id="one", objective="define", affected_paths=["src/a.py"]),
            PlanStep(
                id="two",
                objective="update caller",
                affected_paths=["src/b.py"],
                dependencies=["one"],
            ),
        ],
    )


def ok_result() -> ToolResult:
    return ToolResult(status="ok")


def campaign(repo: Path, *, verify=ok_result, audit=ok_result) -> RefactorCampaign:
    tools = DeveloperTools(repo)
    return RefactorCampaign(
        tools=tools,
        plan=make_plan(),
        submit=tools.submit_patch,
        verify_step=lambda _step: verify(),
        completion_audit=lambda _plan: audit(),
    )


def test_success_tracks_verified_steps_and_preserves_changes(repo: Path) -> None:
    execution = campaign(repo)
    assert execution.execute_step(
        PatchPayload({"src/a.py": "RENAMED = 1\n"}, "rename", "one")
    ).status == "step_verified"
    assert execution.execute_step(
        PatchPayload({"src/b.py": "from .a import RENAMED\n"}, "rename", "two")
    ).status == "step_verified"
    result = execution.finish()
    assert result.ok
    assert result.completed_steps == ("one", "two")
    assert (repo / "src/a.py").read_text() == "RENAMED = 1\n"


def test_verification_failure_rolls_back_entire_campaign(repo: Path) -> None:
    calls = 0

    def verify() -> ToolResult:
        nonlocal calls
        calls += 1
        return ok_result() if calls == 1 else ToolResult(status="error", error="gate failed")

    execution = campaign(repo, verify=verify)
    execution.execute_step(PatchPayload({"src/a.py": "RENAMED = 1\n"}, "rename", "one"))
    result = execution.execute_step(
        PatchPayload({"src/b.py": "broken\n", "src/new.py": "new\n"}, "rename", "two")
    )
    # The extra path violates the plan before submission, but still exercises the
    # campaign-wide transaction: the already verified first step is reverted.
    assert result.status == "invalid_patch"
    assert result.rollback_complete
    assert (repo / "src/a.py").read_text() == "VALUE = 1\n"
    assert not (repo / "src/new.py").exists()


def test_later_gate_failure_restores_prior_verified_step(repo: Path) -> None:
    calls = 0

    def verify() -> ToolResult:
        nonlocal calls
        calls += 1
        return ok_result() if calls == 1 else ToolResult(status="error", error="typecheck")

    execution = campaign(repo, verify=verify)
    execution.execute_step(PatchPayload({"src/a.py": "RENAMED = 1\n"}, "rename", "one"))
    result = execution.execute_step(PatchPayload({"src/b.py": "broken\n"}, "rename", "two"))
    assert result.status == "verification_failed"
    assert result.rollback_complete
    assert (repo / "src/a.py").read_text() == "VALUE = 1\n"
    assert (repo / "src/b.py").read_text() == "from .a import VALUE\n"


def test_finish_rejects_incomplete_campaign_and_rolls_back(repo: Path) -> None:
    execution = campaign(repo)
    execution.execute_step(PatchPayload({"src/a.py": "RENAMED = 1\n"}, "rename", "one"))
    result = execution.finish()
    assert result.status == "incomplete"
    assert result.rollback_complete
    assert (repo / "src/a.py").read_text() == "VALUE = 1\n"


def test_completion_audit_failure_rolls_back_all_steps(repo: Path) -> None:
    execution = campaign(
        repo,
        audit=lambda: ToolResult(
            status="error", error="call site missing", error_class="CompletionAuditFailure"
        ),
    )
    execution.execute_step(PatchPayload({"src/a.py": "RENAMED = 1\n"}, "rename", "one"))
    execution.execute_step(
        PatchPayload({"src/b.py": "from .a import RENAMED\n"}, "rename", "two")
    )
    result = execution.finish()
    assert result.status == "completion_audit_failed"
    assert result.rollback_complete
    assert result.audit is not None
    assert (repo / "src/a.py").read_text() == "VALUE = 1\n"


def test_dependencies_and_scope_are_enforced(repo: Path) -> None:
    execution = campaign(repo)
    result = execution.execute_step(PatchPayload({"src/b.py": "changed\n"}, "rename", "two"))
    assert result.status == "invalid_transition"
    assert result.rollback_complete


def test_test_file_mutation_is_rejected_by_shared_tools(repo: Path) -> None:
    plan = make_plan()
    plan.steps[0].affected_paths.append("tests/test_a.py")
    tools = DeveloperTools(repo)
    execution = RefactorCampaign(
        tools=tools,
        plan=plan,
        submit=tools.submit_patch,
        verify_step=lambda _step: ok_result(),
        completion_audit=lambda _plan: ok_result(),
    )
    result = execution.execute_step(
        PatchPayload({"tests/test_a.py": "cheat = True\n"}, "rename", "one")
    )
    assert result.status == "submission_failed"
    assert result.rollback_complete
    assert (repo / "tests/test_a.py").read_text() == "def test_value(): pass\n"
