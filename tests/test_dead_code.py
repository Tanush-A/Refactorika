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

# A private symbol pulled in via getattr() is a real reflection site -> low.
NAME_IN_GETATTR = """\
def public_api(obj):
    return getattr(obj, "_maybe_dynamic")()

def _maybe_dynamic():
    return 42
"""

# A private symbol whose name merely appears in a plain string/comment is NOT a
# reflection site -> must NOT be demoted to low (B2 narrowing).
NAME_IN_PLAIN_STRING = """\
def _really_dead():
    return 42

DESCRIPTION = "this mentions _really_dead in prose"
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


def test_getattr_reference_is_low_confidence(tmp_path: Path) -> None:
    """A name resolved via getattr("name") is a real reflection site -> low."""
    (tmp_path / "mod.py").write_text(NAME_IN_GETATTR)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    dead = {d["name"].split(".")[-1]: d for d in result["dead_symbols"]}
    assert "_maybe_dynamic" in dead
    assert dead["_maybe_dynamic"]["confidence"] == "low"


def test_name_in_plain_string_is_not_demoted(tmp_path: Path) -> None:
    """B2: a name merely appearing in a plain string is NOT a reflection site.

    The old over-broad check demoted any symbol whose name appeared in any
    string/comment to ``low``. A private, unreferenced symbol must stay ``high``.
    """
    (tmp_path / "mod.py").write_text(NAME_IN_PLAIN_STRING)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    dead = {d["name"].split(".")[-1]: d for d in result["dead_symbols"]}
    assert "_really_dead" in dead
    assert dead["_really_dead"]["confidence"] == "high"


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


# B1: a truly-dead private symbol must NOT be masked by a same-named symbol in
# another module that IS reached. The old resolver credited a bare call to the
# first same-named node it found, making genuinely-dead code look alive.
SAME_NAME_DEAD = """\
def _process():
    return 1
"""

SAME_NAME_ALIVE = """\
def run():
    return _process()

def _process():
    return 2
"""


def test_same_named_symbol_does_not_mask_dead_code(tmp_path: Path) -> None:
    (tmp_path / "mod_a.py").write_text(SAME_NAME_DEAD)
    (tmp_path / "mod_b.py").write_text(SAME_NAME_ALIVE)
    result = find_dead_code(str(tmp_path), _storage(tmp_path))
    dead = {d["name"]: d for d in result["dead_symbols"]}

    # mod_a._process is genuinely dead -> still detected at high confidence.
    assert "mod_a._process" in dead
    assert dead["mod_a._process"]["confidence"] == "high"
    assert dead["mod_a._process"]["rank"] == 90

    # mod_b._process is reached from the public entry point run() -> NOT dead.
    assert "mod_b._process" not in dead
