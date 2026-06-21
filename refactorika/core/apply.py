"""apply_and_verify / apply_and_verify_multi: atomic mutation with gate stack."""

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
    """Single-file atomic edit. Delegates to apply_and_verify_multi."""
    return apply_and_verify_multi({path: new_content}, refactor_kind, storage)


def apply_and_verify_multi(
    edits: dict[str, str], refactor_kind: str, storage: Storage
) -> EditRecord:
    """Multi-file atomic edit: all-or-nothing gate stack, one commit."""
    paths = {p: Path(p).resolve() for p in edits}
    first_path = next(iter(paths.values()))
    repo = _git_root(first_path)

    originals = {p: rp.read_text() for p, rp in paths.items()}
    combined_diff = "\n".join(
        _make_diff(originals[p], edits[p], Path(p).name) for p in edits
    )
    file_strs = [str(rp) for rp in paths.values()]
    retries = storage.count_attempts(str(first_path))

    record = EditRecord(
        file=str(first_path),
        refactor_kind=refactor_kind,
        retries=retries,
        diff=combined_diff,
        files=file_strs,
    )
    checks = record.checks

    # Gate 1 — parse all new contents before touching disk.
    for p, new_content in edits.items():
        ok, detail = parse_gate(new_content)
        if ok is False:
            checks.parse = False
            return _finalize(record, "rolled-back", f"parse failed for {p}: {detail}", storage)
    checks.parse = True

    # Capture ruff baselines before writing.
    baselines = {p: ruff_baseline(rp) for p, rp in paths.items()}

    # Write all files.
    for p, new_content in edits.items():
        paths[p].write_text(new_content)

    try:
        # Gate 2 — lint each touched file.
        for p, rp in paths.items():
            ok, detail = lint_gate(rp, baselines[p])
            if ok is False:
                checks.lint = False
                return _rollback(record, paths, originals, f"lint failed for {p}: {detail}", storage)
        checks.lint = True

        # Gate 3 — typecheck each touched file.
        for p, rp in paths.items():
            ok, detail = typecheck_gate(rp)
            if ok is False:
                checks.typecheck = False
                return _rollback(record, paths, originals, f"typecheck failed for {p}: {detail}", storage)
        checks.typecheck = True

        # Gate 4 — behavior: one pytest run over the whole repo.
        ok, detail = test_gate(repo)
        checks.tests = ok
        if ok is False:
            return _rollback(record, paths, originals, detail, storage)

    except Exception as exc:  # noqa: BLE001
        return _rollback(record, paths, originals, f"gate crashed: {exc}", storage)

    # All gates passed — commit all files in one commit.
    _commit_multi(repo, list(paths.values()), refactor_kind)
    return _finalize(record, "committed", None, storage)


def _commit_multi(repo: Path, resolved_paths: list[Path], refactor_kind: str) -> None:
    for rp in resolved_paths:
        subprocess.run(["git", "-C", str(repo), "add", str(rp)], capture_output=True)
    names = "+".join(rp.name for rp in resolved_paths)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", f"refactor({refactor_kind}): {names}"],
        capture_output=True,
    )


def _rollback(
    record: EditRecord,
    paths: dict[str, Path],
    originals: dict[str, str],
    reason: str,
    storage: Storage,
) -> EditRecord:
    for p, rp in paths.items():
        rp.write_text(originals[p])
    return _finalize(record, "rolled-back", reason, storage)


def _finalize(record: EditRecord, status: str, reason: str | None, storage: Storage) -> EditRecord:
    record.status = status  # type: ignore[assignment]
    record.failure_reason = reason
    storage.append_log(record.to_dict())
    return record
