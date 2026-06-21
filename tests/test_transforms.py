"""Deterministic transform engines: reference-correct, pure (no disk writes)."""

from __future__ import annotations

from pathlib import Path

from refactorika.core.schema import TransformSpec
from refactorika.graph.resolver import build_graph
from refactorika.transforms.base import dispatch
from refactorika.transforms.cleanup import clean_source
from refactorika.transforms.dead_code import remove_symbol_from_source
from refactorika.transforms.node_replace import replace_function_in_source


def _write(tmp_path: Path, files: dict[str, str]) -> str:
    for name, src in files.items():
        p = tmp_path / name
        p.write_text(src)
    return str(tmp_path)


def test_rename_updates_definition_and_all_call_sites(tmp_path):
    root = _write(tmp_path, {
        "util.py": "def helper(x):\n    return x + 1\n",
        "app.py": "from util import helper\n\ndef run():\n    return helper(1) + helper(2)\n",
    })
    g = build_graph(root)
    spec = TransformSpec(kind="rename", target="util.helper",
                         params={"new_name": "increment"})
    edits = dispatch(spec, root, g)
    # both files are rewritten...
    assert any(p.endswith("util.py") for p in edits)
    assert any(p.endswith("app.py") for p in edits)
    util_new = next(v for p, v in edits.items() if p.endswith("util.py"))
    app_new = next(v for p, v in edits.items() if p.endswith("app.py"))
    assert "def increment(" in util_new
    assert "helper" not in app_new and app_new.count("increment") == 3  # import + 2 calls
    # ...and the engine did NOT touch disk
    assert "helper" in (tmp_path / "util.py").read_text()


def test_rename_does_not_touch_same_named_symbol_elsewhere(tmp_path):
    root = _write(tmp_path, {
        "a.py": "def process():\n    return 'a'\n",
        "b.py": "def process():\n    return 'b'\n",
        "main.py": "from a import process\n\ndef go():\n    return process()\n",
    })
    g = build_graph(root)
    spec = TransformSpec(kind="rename", target="a.process",
                         params={"new_name": "process_a"})
    edits = dispatch(spec, root, g)
    # b.py must be untouched (its process() is a different binding)
    assert not any(p.endswith("b.py") for p in edits)
    main_new = next(v for p, v in edits.items() if p.endswith("main.py"))
    assert "process_a()" in main_new


def test_cleanup_removes_unused_and_duplicate_imports(tmp_path):
    src = "import os\nimport sys\nimport os\n\n\ndef f():\n    return 1\n"
    cleaned = clean_source(src, "m.py")
    assert "import os" not in cleaned
    assert "import sys" not in cleaned
    assert "def f():" in cleaned


def test_dead_code_removes_only_named_symbol(tmp_path):
    src = (
        "def keep():\n    return 1\n\n\n"
        "def _dead():\n    return 2\n\n\n"
        "def also_keep():\n    return 3\n"
    )
    out = remove_symbol_from_source(src, "_dead")
    assert "_dead" not in out
    assert "def keep():" in out
    assert "def also_keep():" in out


def test_node_replace_swaps_function_body(tmp_path):
    src = "def big():\n    return 1 + 2 + 3\n"
    new = "def _part():\n    return 6\n\n\ndef big():\n    return _part()\n"
    out = replace_function_in_source(src, "big", new)
    assert "_part" in out
    assert "return _part()" in out


def test_rename_noop_when_same_name(tmp_path):
    root = _write(tmp_path, {"m.py": "def f():\n    return 1\n"})
    g = build_graph(root)
    spec = TransformSpec(kind="rename", target="m.f", params={"new_name": "f"})
    assert dispatch(spec, root, g) == {}
