"""Fast unit tests for the pieces with no git/subprocess dependency."""

from pathlib import Path

from refactorika.core.analyze import analyze_file
from refactorika.core.gates import parse_gate
from refactorika.core.schema import (
    AuditEntry,
    Opportunity,
    Plan,
    PlanTask,
    RepoAudit,
)
from refactorika.core.storage import Storage


def test_parse_gate_accepts_valid() -> None:
    ok, _ = parse_gate("def f(x: int) -> int:\n    return x + 1\n")
    assert ok is True


def test_parse_gate_rejects_syntax_error() -> None:
    ok, detail = parse_gate("def f(:\n    return\n")
    assert ok is False
    assert "syntax" in detail


def test_analyze_finds_smells(tmp_path: Path) -> None:
    f = tmp_path / "messy.py"
    f.write_text(
        "import os\nimport os\n"
        "def g(a):\n"
        "    if a:\n        if a > 1:\n            if a > 2:\n                if a > 3:\n"
        "                    return a\n"
    )
    result = analyze_file(str(f))
    kinds = {o.kind for o in result.opportunities}
    assert "reorder_imports" in kinds  # duplicate import
    assert "flatten_nesting" in kinds  # depth 4 > 3


def test_storage_json_fallback(tmp_path: Path) -> None:
    s = Storage(redis_url=None, json_path=tmp_path / "state.json")
    assert s.backend == "json"
    s.append_log({"file": "a.py", "status": "committed", "refactor_kind": "x"})
    s.cache_set("k", {"v": 1})
    assert s.get_log()[0]["file"] == "a.py"
    assert s.cache_get("k") == {"v": 1}
    assert s.count_attempts("a.py") == 0  # committed doesn't count as a retry


def test_v3_schema_roundtrip() -> None:
    o = Opportunity("flatten_nesting", "g", "deep nesting", 10)

    # AuditEntry / RepoAudit: to_dict only, nested opportunities survive.
    entry = AuditEntry(file="a.py", opportunities=[o], score=10)
    assert entry.to_dict()["opportunities"][0]["kind"] == "flatten_nesting"
    audit = RepoAudit(
        repo="r",
        files_scanned=1,
        total_opportunities=1,
        by_kind={"flatten_nesting": 1},
        dominant_finding="flatten_nesting",
        entries=[entry],
    )
    ad = audit.to_dict()
    assert ad["by_kind"] == {"flatten_nesting": 1}
    assert ad["entries"][0]["opportunities"][0]["detail"] == "deep nesting"

    # PlanTask: to_dict -> from_dict, nested Opportunity list survives.
    task = PlanTask(file="a.py", opportunities=[o], dependents=["b.py"], order=0)
    task2 = PlanTask.from_dict(task.to_dict())
    assert task2.opportunities[0].kind == "flatten_nesting"
    assert task2.opportunities[0].rank == 10
    assert task2.dependents == ["b.py"]

    # Plan: defaults, then to_dict -> from_dict round-trip.
    plan = Plan(repo="r", dominant_finding=None, tasks=[task])
    assert plan.confirmed is False
    assert plan.decision is None
    plan2 = Plan.from_dict(plan.to_dict())
    assert plan2.repo == "r"
    assert plan2.dominant_finding is None
    assert plan2.confirmed is False
    assert plan2.decision is None
    assert plan2.tasks[0].opportunities[0].kind == "flatten_nesting"
