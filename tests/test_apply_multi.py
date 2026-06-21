"""Tests for apply_and_verify_multi: atomic two-file apply, rollback on failure."""

from pathlib import Path

from refactorika.core.apply import apply_and_verify_multi
from refactorika.core.storage import Storage

GOOD_A = "def foo(x: int) -> int:\n    return x + 1\n"
GOOD_B = "def bar(y: int) -> int:\n    return y * 2\n"
BAD_SYNTAX = "def broken(:\n    return\n"


def _storage(tmp_path: Path) -> Storage:
    return Storage(redis_url=None, json_path=tmp_path / "state.json")


def test_two_file_green_commit(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("x = 1\n")
    b.write_text("y = 2\n")

    storage = _storage(tmp_path)
    record = apply_and_verify_multi(
        {str(a): GOOD_A, str(b): GOOD_B},
        "extract_helper",
        storage,
    )
    # parse gate must pass; lint/type/test may skip (no project deps)
    assert record.checks.parse is True
    assert record.files == [str(a), str(b)]
    # Content written
    assert a.read_text() == GOOD_A
    assert b.read_text() == GOOD_B


def test_bad_syntax_in_one_restores_both(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    original_a = "x = 1\n"
    original_b = "y = 2\n"
    a.write_text(original_a)
    b.write_text(original_b)

    storage = _storage(tmp_path)
    record = apply_and_verify_multi(
        {str(a): BAD_SYNTAX, str(b): GOOD_B},
        "flatten_nesting",
        storage,
    )
    assert record.checks.parse is False
    assert record.status == "rolled-back"
    # Both files must be restored
    assert a.read_text() == original_a
    assert b.read_text() == original_b


def test_edit_record_files_field(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("pass\n")
    b.write_text("pass\n")

    storage = _storage(tmp_path)
    record = apply_and_verify_multi(
        {str(a): GOOD_A, str(b): GOOD_B},
        "consolidate_duplicate",
        storage,
    )
    assert str(a) in record.files
    assert str(b) in record.files
    # to_dict includes both
    d = record.to_dict()
    assert str(a) in d["files"]
    assert str(b) in d["files"]


def test_single_file_delegate(tmp_path: Path) -> None:
    """apply_and_verify (single-file) delegates to apply_and_verify_multi."""
    from refactorika.core.apply import apply_and_verify  # noqa: PLC0415
    a = tmp_path / "a.py"
    a.write_text("pass\n")
    storage = _storage(tmp_path)
    record = apply_and_verify(str(a), GOOD_A, "flatten_nesting", storage)
    assert record.files == [str(a)]
    assert record.checks.parse is True
