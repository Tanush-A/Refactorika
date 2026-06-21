"""The verified spine: checker commit/revert + orchestrator end-to-end.

The revert test is the trust money-shot — a type-clean but behavior-breaking edit must
be caught by the test gate and rolled back, leaving the file exactly as it was.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from refactorika.core.storage import Storage
from refactorika.pipeline.checker import Checker, impacted_test_node_ids
from refactorika.pipeline.orchestrator import run_pipeline


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, src in files.items():
        (tmp_path / name).write_text(src)
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "base"], capture_output=True)
    return tmp_path


_LIB = "def tax(total):\n    return total * 0.08\n"
_TEST = (
    "from lib import tax\n\n"
    "def test_tax():\n    assert tax(100) == 8.0\n"
)


def test_checker_commits_clean_edit(tmp_path):
    repo = _git_repo(tmp_path, {"lib.py": _LIB, "test_lib.py": _TEST})
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    checker = Checker(str(repo), storage=storage)
    # an equivalent rewrite (same behavior) should pass all gates and commit
    new = "def tax(total):\n    rate = 0.08\n    return total * rate\n"
    rec = checker.verify_apply({str(repo / "lib.py"): new}, "cleanup")
    assert rec.status == "committed"
    assert "rate = 0.08" in (repo / "lib.py").read_text()


def test_checker_reverts_behavior_break(tmp_path):
    repo = _git_repo(tmp_path, {"lib.py": _LIB, "test_lib.py": _TEST})
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    checker = Checker(str(repo), storage=storage)
    original = (repo / "lib.py").read_text()
    # type-clean but wrong: changes the tax rate, breaking test_tax
    broken = "def tax(total):\n    return total * 0.05\n"
    rec = checker.verify_apply({str(repo / "lib.py"): broken}, "cleanup")
    assert rec.status == "rolled-back"
    assert rec.checks.tests is False
    # the file is restored byte-for-byte
    assert (repo / "lib.py").read_text() == original


def test_impacted_test_selection_maps_to_node_ids(tmp_path):
    from refactorika.graph.resolver import build_graph
    repo = _git_repo(tmp_path, {"lib.py": _LIB, "test_lib.py": _TEST})
    g = build_graph(str(repo))
    from refactorika.graph.order import impact_of
    impact = sorted(impact_of(g, "lib.tax"))
    node_ids = impacted_test_node_ids(g, str(repo), impact)
    assert node_ids == ["test_lib.py::test_tax"]


def test_orchestrator_removes_dead_code_and_keeps_tests_green(tmp_path):
    src = (
        "def _dead():\n    return 0\n\n\n"
        "def used():\n    return 1\n"
    )
    test = "from lib import used\n\ndef test_used():\n    assert used() == 1\n"
    fixture = _git_repo(tmp_path, {"lib.py": src, "test_lib.py": test})
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    res = run_pipeline(str(fixture), apply=False, storage=storage)
    assert res.metrics_before["dead_symbols"] == 1
    assert res.metrics_after["dead_symbols"] == 0
    kinds = [r["refactor_kind"] for r in res.records if r["status"] == "committed"]
    assert "remove_dead_code" in kinds


def test_demo_repo_deterministic_run_is_clean(tmp_path):
    """Guard the demo: dead-code removed root-to-leaf + cleanup, no reverts, finale green."""
    import shutil
    from pathlib import Path

    demo = Path(__file__).resolve().parent.parent / "demo_repo"
    target = tmp_path / "demo_repo"
    shutil.copytree(demo, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    res = run_pipeline(str(target), apply=False, storage=storage)

    assert res.baseline_tests is True
    assert res.finale_tests is True
    assert all(r["status"] == "committed" for r in res.records)  # no reverts
    kinds = [r["refactor_kind"] for r in res.records]
    assert kinds.count("remove_dead_code") == 2  # _legacy_discount + orphaned _round_money
    assert "cleanup" in kinds  # unused `import json` removed
    assert res.metrics_after["dead_symbols"] == 0
    assert res.metrics_after["sloc"] < res.metrics_before["sloc"]


def test_orchestrator_applies_in_place_when_apply_true(tmp_path):
    src = "def _dead():\n    return 0\n\n\ndef used():\n    return 1\n"
    test = "from lib import used\n\ndef test_used():\n    assert used() == 1\n"
    fixture = _git_repo(tmp_path, {"lib.py": src, "test_lib.py": test})
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    res = run_pipeline(str(fixture), apply=True, storage=storage)
    assert res.applied is True
    # in --apply mode the real file is changed
    assert "_dead" not in (fixture / "lib.py").read_text()
