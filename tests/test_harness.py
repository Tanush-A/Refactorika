from __future__ import annotations

from pathlib import Path

from refactorika.harness import mark_escalated, verify_edits


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "tests" / "gate").mkdir(parents=True)
    (tmp_path / "app.py").write_text("def value() -> int:\n    return 1\n")
    (tmp_path / "tests" / "gate" / "test_app.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value() > 0\n"
    )
    (tmp_path / "pyrightconfig.json").write_text(
        '{"include":["app.py"],"typeCheckingMode":"strict"}'
    )
    return tmp_path


def test_green_atomic_edit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    record = verify_edits(
        repo,
        {"app.py": "def value() -> int:\n    return 2\n"},
        required_gates=("lint", "typecheck", "tests"),
    )
    assert record.status == "committed"
    assert repo.joinpath("app.py").read_text().endswith("return 2\n")
    assert record.checks.parse is True
    assert record.checks.tests is True


def test_one_bad_file_rolls_back_all(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "other.py").write_text("X = 1\n")
    before_app = (repo / "app.py").read_text()
    before_other = (repo / "other.py").read_text()
    record = verify_edits(
        repo,
        {"app.py": "def value(:\n", "other.py": "X = 2\n"},
    )
    assert record.status == "rolled-back"
    assert record.checks.parse is False
    assert (repo / "app.py").read_text() == before_app
    assert (repo / "other.py").read_text() == before_other


def test_behavior_failure_rolls_back(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = (repo / "app.py").read_text()
    record = verify_edits(
        repo,
        {"app.py": "def value() -> int:\n    return -1\n"},
        required_gates=("tests",),
    )
    assert record.status == "rolled-back"
    assert record.checks.tests is False
    assert (repo / "app.py").read_text() == before
    assert mark_escalated(record).status == "skipped-needs-human"


def test_rejects_path_escape(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    try:
        verify_edits(repo, {"../escape.py": "pass\n"})
    except ValueError as exc:
        assert "escapes repository" in str(exc)
    else:
        raise AssertionError("path escape accepted")


def test_new_python_file_is_supported_and_removed_on_rollback(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    created = repo / "helper.py"

    green = verify_edits(
        repo, {"helper.py": "def doubled(value: int) -> int:\n    return value * 2\n"}
    )
    assert green.status == "committed"
    assert created.is_file()

    created.unlink()
    rejected = verify_edits(
        repo,
        {
            "helper.py": "def doubled(value: int) -> int:\n    return value * 2\n",
            "app.py": "def value() -> int:\n    return -1\n",
        },
        required_gates=("tests",),
    )
    assert rejected.status == "rolled-back"
    assert not created.exists()
