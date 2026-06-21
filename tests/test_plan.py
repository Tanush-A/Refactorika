"""Tests for dependency-ordered planning."""

from pathlib import Path

from refactorika.analysis.audit import build_plan
from refactorika.core.schema import Plan
from refactorika.core.storage import Storage

# Each file has a deeply-nested function so it's a deviating file (>=1 opportunity).
MESSY_BODY = """\
def deep(a):
    if a:
        if a > 1:
            if a > 2:
                if a > 3:
                    return {ret}
    return 0
"""


def _storage(tmp_path: Path) -> Storage:
    return Storage(redis_url=None, json_path=tmp_path / "state.json")


def test_plan_orders_fewest_dependents_first(tmp_path: Path) -> None:
    # a.shared is imported+called by b and c -> a has 2 dependents (highest blast radius).
    (tmp_path / "a.py").write_text(
        "def shared():\n    return 1\n\n\n" + MESSY_BODY.format(ret="shared()")
    )
    (tmp_path / "b.py").write_text(
        "from a import shared\n\n\n" + MESSY_BODY.format(ret="shared()")
    )
    (tmp_path / "c.py").write_text(
        "from a import shared\n\n\n" + MESSY_BODY.format(ret="shared()")
    )

    storage = _storage(tmp_path)
    plan = build_plan(str(tmp_path), storage)

    names = [Path(t.file).stem for t in plan.tasks]
    # a (2 dependents) must be ordered LAST; b and c (0 dependents) first.
    assert names[-1] == "a"
    assert set(names[:-1]) == {"b", "c"}
    # order is contiguous 0..n-1
    assert [t.order for t in plan.tasks] == list(range(len(plan.tasks)))
    # the task for 'a' carries its dependents
    a_task = next(t for t in plan.tasks if Path(t.file).stem == "a")
    assert a_task.dependents == ["b", "c"]


def test_plan_is_persisted_and_reloadable(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(MESSY_BODY.format(ret="a"))
    storage = _storage(tmp_path)
    plan = build_plan(str(tmp_path), storage)

    raw = storage.load_plan()
    assert raw is not None
    reloaded = Plan.from_dict(raw)
    assert reloaded.repo == plan.repo
    assert len(reloaded.tasks) == len(plan.tasks)
    assert reloaded.confirmed is False
