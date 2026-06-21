"""Atomic, project-scoped verification for agent-proposed Python edits.

The benchmark calls this public contract directly.  Callers own retry policy;
this module owns all-or-nothing writes, gate ordering, and rollback.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

GateValue = bool | None
Status = Literal["committed", "rolled-back", "skipped-needs-human"]


@dataclass
class GateChecks:
    parse: GateValue = None
    lint: GateValue = None
    typecheck: GateValue = None
    tests: GateValue = None
    callsite_sweep: GateValue = None
    handled_result: GateValue = None


@dataclass
class VerificationRecord:
    files: list[str]
    checks: GateChecks = field(default_factory=GateChecks)
    status: Status = "rolled-back"
    failure_reason: str | None = None
    diff: str = ""
    gate_details: dict[str, str] = field(default_factory=dict)
    retries: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["checks"] = asdict(self.checks)
        return data


def _tool(name: str) -> str | None:
    found = shutil.which(name)
    if found is None:
        sibling = Path(sys.executable).parent / name
        found = str(sibling) if sibling.is_file() else None
    return os.path.abspath(found) if found else None


def _run(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stdout + "\n" + result.stderr).strip().splitlines()
    return output[-1] if output else f"exit {result.returncode}"


def _diff(relative: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


def _resolve_edits(repo: Path, edits: dict[str, str]) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for relative in edits:
        candidate = (repo / relative).resolve()
        try:
            candidate.relative_to(repo)
        except ValueError as exc:
            raise ValueError(f"edit escapes repository: {relative}") from exc
        if candidate.suffix != ".py":
            raise ValueError(f"only Python edits are supported: {relative}")
        if candidate.exists() and not candidate.is_file():
            raise ValueError(f"edited path is not a file: {relative}")
        if not candidate.parent.is_dir():
            raise ValueError(f"parent directory does not exist: {relative}")
        resolved[relative] = candidate
    if not resolved:
        raise ValueError("at least one edit is required")
    return resolved


def _parse_all(edits: dict[str, str]) -> tuple[bool, str]:
    for relative, content in edits.items():
        try:
            ast.parse(content, filename=relative)
        except SyntaxError as exc:
            return False, f"{relative}:{exc.lineno}: {exc.msg}"
    return True, f"parsed {len(edits)} file(s)"


def _ruff_count(ruff: str, repo: Path, paths: list[str]) -> tuple[int | None, str]:
    result = _run([ruff, "check", "--output-format", "json", *paths], repo, 120)
    try:
        violations = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None, "ruff returned invalid JSON"
    return len(violations), f"{len(violations)} violation(s)"


def verify_edits(
    repo: str | Path,
    edits: dict[str, str],
    *,
    test_command: list[str] | None = None,
    required_gates: tuple[str, ...] = (),
    retries: int = 0,
    timeout: int = 180,
) -> VerificationRecord:
    """Apply all edits atomically and run parse -> lint -> type -> tests.

    Missing tools are explicit ``None`` checks. If a missing gate is named in
    ``required_gates``, the proposal is rolled back. Files are never committed
    to git here: ``committed`` means accepted in the supplied isolated worktree.
    """

    root = Path(repo).resolve()
    paths = _resolve_edits(root, edits)
    originals = {rel: path.read_text() if path.is_file() else None for rel, path in paths.items()}
    record = VerificationRecord(
        files=sorted(paths),
        diff="\n".join(_diff(rel, originals[rel] or "", edits[rel]) for rel in sorted(paths)),
        retries=retries,
    )

    def reject(gate: str, reason: str) -> VerificationRecord:
        for rel, path in paths.items():
            original = originals[rel]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original)
        record.status = "rolled-back"
        record.failure_reason = f"{gate}: {reason}"
        record.gate_details[gate] = reason
        return record

    parsed, detail = _parse_all(edits)
    record.checks.parse = parsed
    record.gate_details["parse"] = detail
    if not parsed:
        return reject("parse", detail)

    relative_paths = sorted(paths)
    existing_paths = sorted(rel for rel, original in originals.items() if original is not None)
    ruff = _tool("ruff")
    baselines: int | None = None
    if ruff:
        baselines, detail = (
            _ruff_count(ruff, root, existing_paths) if existing_paths else (0, "0 violation(s)")
        )
        if baselines is None:
            record.checks.lint = False
            return reject("lint", detail)

    for rel, path in paths.items():
        path.write_text(edits[rel])

    try:
        if ruff is None:
            record.checks.lint = None
            record.gate_details["lint"] = "ruff unavailable"
            if "lint" in required_gates:
                return reject("lint", "required tool ruff unavailable")
        else:
            count, detail = _ruff_count(ruff, root, relative_paths)
            if count is None:
                record.checks.lint = False
                return reject("lint", detail)
            record.checks.lint = count <= (baselines or 0)
            record.gate_details["lint"] = f"{detail}; baseline {baselines}"
            if record.checks.lint is False:
                return reject("lint", record.gate_details["lint"])

        pyright = _tool("pyright")
        if pyright is None:
            record.checks.typecheck = None
            record.gate_details["typecheck"] = "pyright unavailable"
            if "typecheck" in required_gates:
                return reject("typecheck", "required tool pyright unavailable")
        else:
            result = _run([pyright, "--outputjson"], root, timeout)
            try:
                pyright_output = json.loads(result.stdout)
                errors = int(pyright_output.get("summary", {}).get("errorCount", 0))
            except (ValueError, TypeError, json.JSONDecodeError):
                return reject("typecheck", "pyright returned invalid JSON")
            record.checks.typecheck = errors == 0
            diagnostics = []
            for diagnostic in pyright_output.get("generalDiagnostics", []):
                if diagnostic.get("severity") != "error":
                    continue
                file = diagnostic.get("file", "unknown file")
                try:
                    file = str(Path(file).resolve().relative_to(root))
                except ValueError:
                    pass
                start = diagnostic.get("range", {}).get("start", {})
                line = int(start.get("line", 0)) + 1
                message = " ".join(str(diagnostic.get("message", "type error")).split())
                diagnostics.append(f"{file}:{line}: {message}")
            detail = f"{errors} error(s)"
            if diagnostics:
                detail += "; " + " | ".join(diagnostics[:8])
            record.gate_details["typecheck"] = detail
            if errors:
                return reject("typecheck", record.gate_details["typecheck"])

        command = test_command or [sys.executable, "-m", "pytest", "-q", "tests/gate"]
        result = _run(command, root, timeout)
        if result.returncode == 5:
            record.checks.tests = None
            record.gate_details["tests"] = "no tests collected"
            if "tests" in required_gates:
                return reject("tests", "required tests were not collected")
        else:
            record.checks.tests = result.returncode == 0
            record.gate_details["tests"] = _detail(result)
            if result.returncode != 0:
                return reject("tests", record.gate_details["tests"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return reject("gate_crash", f"{type(exc).__name__}: {exc}")

    # These checks require the convention analyzer, which is not implemented on
    # main yet. Their explicit skipped state is part of the frozen contract.
    record.checks.callsite_sweep = None
    record.checks.handled_result = None
    record.gate_details["callsite_sweep"] = "analyzer unavailable"
    record.gate_details["handled_result"] = "analyzer unavailable"
    for gate in ("callsite_sweep", "handled_result"):
        if gate in required_gates:
            return reject(gate, "required analyzer unavailable")

    record.status = "committed"
    return record


def mark_escalated(record: VerificationRecord) -> VerificationRecord:
    """Convert the final rejected attempt into the terminal safe state."""

    if record.status != "rolled-back":
        raise ValueError("only a rolled-back record can be escalated")
    record.status = "skipped-needs-human"
    return record
