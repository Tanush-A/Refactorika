"""Plan persistence tests (JSON fallback backend, single overwritten plan)."""

from pathlib import Path

from refactorika.core.storage import Storage


def _make_storage(tmp_path: Path) -> Storage:
    return Storage(redis_url=None, json_path=tmp_path / "state.json")


def test_load_plan_none_before_save(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    assert s.backend == "json"
    assert s.load_plan() is None


def test_save_plan_round_trips(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    plan = {"repo": "demo", "tasks": []}
    s.save_plan(plan)
    assert s.load_plan() == plan


def test_save_plan_overwrites(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    s.save_plan({"repo": "demo", "tasks": ["first"]})
    s.save_plan({"repo": "demo", "tasks": ["second"]})
    loaded = s.load_plan()
    assert loaded == {"repo": "demo", "tasks": ["second"]}  # latest only, not appended


def test_save_plan_nested_tasks(tmp_path: Path) -> None:
    s = _make_storage(tmp_path)
    plan = {
        "repo": "demo",
        "tasks": [
            {"file": "a.py", "refactor_kind": "split_module", "status": "pending"},
            {"file": "b.py", "refactor_kind": "flatten_nesting", "status": "done"},
        ],
    }
    s.save_plan(plan)
    assert s.load_plan() == plan
