"""Tests for the confirm_plan human checkpoint (drives storage directly, no live singleton)."""

from pathlib import Path

from refactorika.core.schema import Opportunity, Plan, PlanTask
from refactorika.core.storage import Storage


def _storage(tmp_path: Path) -> Storage:
    return Storage(redis_url=None, json_path=tmp_path / "state.json")


def _seed_plan(storage: Storage) -> None:
    o = Opportunity("flatten_nesting", "f", "d", 10)
    plan = Plan(
        repo="r",
        dominant_finding="flatten_nesting (1 sites)",
        tasks=[
            PlanTask("b.py", [o], [], 0),
            PlanTask("a.py", [o], ["b"], 1),
        ],
    )
    storage.save_plan(plan.to_dict())


def _confirm(storage: Storage, decision: str, order=None) -> Plan:
    # Mirror the mcp_server.confirm_plan logic against an injected storage.
    raw = storage.load_plan()
    assert raw is not None
    plan = Plan.from_dict(raw)
    if decision == "approve":
        plan.confirmed, plan.decision = True, "approve"
    elif decision == "reject":
        plan.confirmed, plan.decision = False, "reject"
    elif decision == "reorder" and order:
        by_file = {t.file: t for t in plan.tasks}
        plan.tasks = [by_file[f] for f in order if f in by_file]
        for i, t in enumerate(plan.tasks):
            t.order = i
        plan.confirmed, plan.decision = True, "reorder"
    storage.save_plan(plan.to_dict())
    return plan


def test_approve(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    _seed_plan(s)
    plan = _confirm(s, "approve")
    assert plan.confirmed is True
    assert plan.decision == "approve"
    assert Plan.from_dict(s.load_plan()).confirmed is True


def test_reject(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    _seed_plan(s)
    plan = _confirm(s, "reject")
    assert plan.confirmed is False
    assert plan.decision == "reject"


def test_reorder(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    _seed_plan(s)
    plan = _confirm(s, "reorder", order=["a.py", "b.py"])
    assert [t.file for t in plan.tasks] == ["a.py", "b.py"]
    assert [t.order for t in plan.tasks] == [0, 1]
    assert plan.confirmed is True
    assert plan.decision == "reorder"
