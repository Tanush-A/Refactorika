"""Fast unit tests for the pieces with no git/subprocess dependency."""

from pathlib import Path

from refactorika.core.analyze import analyze_file
from refactorika.core.gates import parse_gate
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
