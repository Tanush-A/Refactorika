"""apply_and_verify: the atomic heart. Snapshot -> write -> gate -> commit/rollback -> log."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

from .gates import lint_gate, parse_gate, ruff_baseline, test_gate, typecheck_gate
from .schema import EditRecord
from .storage import Storage


def _git_root(path: Path) -> Path:
    out = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    return Path(out.stdout.strip()) if out.returncode == 0 else path.parent


def _make_diff(old: str, new: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        )
    )


def apply_and_verify(
    path: str, new_content: str, refactor_kind: str, storage: Storage
) -> EditRecord:
    """Try one structural edit. Working tree is never left dirty: commit on green, restore on fail."""
    p = Path(path).resolve()
    repo = _git_root(p)
    original = p.read_text()
    diff = _make_diff(original, new_content, p.name)
    retries = storage.count_attempts(str(p))

    record = EditRecord(
        file=str(p), refactor_kind=refactor_kind, retries=retries, diff=diff
    )
    checks = record.checks

    # Gate 1 — parse (on content, before touching disk).
    ok, detail = parse_gate(new_content)
    checks.parse = ok
    if ok is False:
        return _finalize(record, "rolled-back", detail, storage)

    baseline = ruff_baseline(p)
    p.write_text(new_content)
    try:
        # Gate 2 — lint (new violations only).
        ok, detail = lint_gate(p, baseline)
        checks.lint = ok
        if ok is False:
            return _rollback(record, p, original, detail, storage)

        # Gate 3 — type.
        ok, detail = typecheck_gate(p)
        checks.typecheck = ok
        if ok is False:
            return _rollback(record, p, original, detail, storage)

        # Gate 4 — behavior. Type-clean != behavior-preserving.
        ok, detail = test_gate(repo)
        checks.tests = ok
        if ok is False:
            return _rollback(record, p, original, detail, storage)

    except Exception as exc:  # noqa: BLE001 — any gate crash must restore the tree
        return _rollback(record, p, original, f"gate crashed: {exc}", storage)

    # All gates passed or were explicitly skipped -> commit.
    _commit(repo, p, refactor_kind)
    return _finalize(record, "committed", None, storage)


def _commit(repo: Path, p: Path, refactor_kind: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", str(p)], capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", f"refactor({refactor_kind}): {p.name}"],
        capture_output=True,
    )


def _rollback(
    record: EditRecord, p: Path, original: str, reason: str, storage: Storage
) -> EditRecord:
    p.write_text(original)  # restore working tree
    return _finalize(record, "rolled-back", reason, storage)


def _finalize(
    record: EditRecord, status, reason, storage: Storage
) -> EditRecord:
    record.status = status
    record.failure_reason = reason
    storage.append_log(record.to_dict())
    return record
