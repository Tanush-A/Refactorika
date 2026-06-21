"""Tests for dead-code detection: confidence levels, reachability, exclusions."""

from pathlib import Path

from refactorika.analysis.dead_code import find_dead_code
from refactorika.core.storage import Storage


def _storage(tmp_path: Path) -> Storage:
    return Storage(redis_url=None, json_path=tmp_path / "state.json")


PRIVATE_DEAD = """\
def public_api():
    return 1

def _unused_helper():
    return 2
"""

PUBLIC_DEAD = """\
def compute():
    return 1

def unused_public():
    return 2
"""

NAME_IN_STRING = """\
def maybe_dynamic():
    return 42

x = "maybe_dynamic"
"""

REACHABLE = """\
def entry():
    return helper()

def helper():
    return 99
"""


def test_private_unused_is_high_confidence(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(PRIVATE_DEAD)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    dead = {d["name"].split(".")[-1]: d for d in result["dead_symbols"]}
    assert "_unused_helper" in dead
    assert dead["_unused_helper"]["confidence"] == "high"
    assert dead["_unused_helper"]["rank"] == 90


def test_public_symbol_not_flagged_high(tmp_path: Path) -> None:
    """Public symbols are treated as conservative entry points — never flagged high confidence."""
    (tmp_path / "mod.py").write_text(PUBLIC_DEAD)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    dead = {d["name"].split(".")[-1]: d for d in result["dead_symbols"]}
    # Public symbols are entry points; any that appear are at most medium confidence
    for name, sym in dead.items():
        if not name.startswith("_"):
            assert sym["confidence"] in ("medium", "low")


def test_name_in_string_is_low_confidence(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(NAME_IN_STRING)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    dead = {d["name"].split(".")[-1]: d for d in result["dead_symbols"]}
    # maybe_dynamic's name appears in a string — low confidence at most
    if "maybe_dynamic" in dead:
        assert dead["maybe_dynamic"]["confidence"] == "low"


def test_reachable_symbol_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(REACHABLE)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    dead_names = {d["name"].split(".")[-1] for d in result["dead_symbols"]}
    # helper is called by entry, which is a public entry point
    assert "helper" not in dead_names


def test_entry_points_listed(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(REACHABLE)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    assert len(result["entry_points"]) > 0
    assert "path" in result
    assert "dead_symbols" in result
