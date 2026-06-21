"""Gate behavior — especially that typecheck rejects only NEW type errors."""

from pathlib import Path

import pytest

from refactorika.core.gates import _tool, pyright_baseline, typecheck_gate

# A function that returns None against an -> int annotation: a pyright error.
PREEXISTING_TYPE_ERROR = "def f() -> int:\n    return None\n"


def test_typecheck_tolerates_preexisting_error(tmp_path: Path) -> None:
    if _tool("pyright") is None:
        pytest.skip("pyright not installed")
    f = tmp_path / "m.py"
    f.write_text(PREEXISTING_TYPE_ERROR)

    baseline = pyright_baseline(f)
    assert baseline >= 1  # the file already has a type error

    # Against its own baseline, a behavior-correct edit that leaves the
    # pre-existing error in place must PASS (it added nothing new).
    ok, _ = typecheck_gate(f, baseline)
    assert ok is True

    # Against a 0 baseline, the same error counts as NEW -> fail.
    ok_strict, detail = typecheck_gate(f, 0)
    assert ok_strict is False
    assert "new type error" in detail


def test_typecheck_clean_file_passes(tmp_path: Path) -> None:
    if _tool("pyright") is None:
        pytest.skip("pyright not installed")
    f = tmp_path / "ok.py"
    f.write_text("def f(x: int) -> int:\n    return x + 1\n")
    ok, _ = typecheck_gate(f, pyright_baseline(f))
    assert ok is True
