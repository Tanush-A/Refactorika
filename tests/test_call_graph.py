"""Tests for CallGraph: symbol collection, edge resolution, entry-point detection."""

from pathlib import Path

from refactorika.analysis.call_graph import CallGraph


SIMPLE = """\
def foo():
    return 1

def bar():
    return foo()
"""

CROSS_FILE_CALLER = """\
from module_b import baz

def caller():
    return baz()
"""

CROSS_FILE_CALLEE = """\
def baz():
    return 42
"""

TEST_FILE = """\
def test_something():
    assert True
"""

MAIN_FILE = """\
def run():
    pass

if __name__ == "__main__":
    run()
"""


def test_same_file_edges(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(SIMPLE)
    cg = CallGraph.build(str(tmp_path))
    assert "mod.foo" in cg.all_symbols()
    assert "mod.bar" in cg.all_symbols()
    edges = cg.edges_from("mod.bar")
    assert "mod.foo" in edges


def test_unresolved_names_ignored(tmp_path: Path) -> None:
    src = "def f():\n    some_external_lib_call()\n"
    (tmp_path / "mod.py").write_text(src)
    cg = CallGraph.build(str(tmp_path))
    # Should not crash; edges may be empty (unresolved)
    assert "mod.f" in cg.all_symbols()


def test_public_symbol_is_entry_point(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(SIMPLE)
    cg = CallGraph.build(str(tmp_path))
    # foo and bar are both public (no _)
    eps = cg.entry_points()
    assert "mod.foo" in eps
    assert "mod.bar" in eps


def test_private_symbol_not_entry_point(tmp_path: Path) -> None:
    src = "def _private():\n    pass\n"
    (tmp_path / "mod.py").write_text(src)
    cg = CallGraph.build(str(tmp_path))
    assert "mod._private" not in cg.entry_points()


def test_test_file_symbols_are_entry_points(tmp_path: Path) -> None:
    (tmp_path / "test_mod.py").write_text(TEST_FILE)
    cg = CallGraph.build(str(tmp_path))
    eps = cg.entry_points()
    assert any("test_something" in ep for ep in eps)


def test_main_block_makes_entry_point(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(MAIN_FILE)
    cg = CallGraph.build(str(tmp_path))
    eps = cg.entry_points()
    assert "mod.run" in eps


def test_call_sites_count(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(SIMPLE)
    cg = CallGraph.build(str(tmp_path))
    # foo is called once (by bar)
    assert cg.call_sites("mod.foo") >= 1
