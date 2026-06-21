"""Verification gates — cheapest first, short-circuit on fail.

Each gate returns ``(result, detail)`` where ``result`` is:
  True  -> passed
  False -> failed (caller rolls back)
  None  -> skipped and recorded (tool missing / no coverage) — never a silent pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

_PY_LANGUAGE = Language(tspython.language())

GateResult = tuple[Optional[bool], str]


def _tool(name: str) -> Optional[str]:
    """Absolute path to a CLI tool, or None. Absolute so it resolves under any subprocess cwd."""
    found = shutil.which(name)
    return os.path.abspath(found) if found else None


def _has_error(node) -> bool:
    if node.type == "ERROR" or node.is_missing:
        return True
    return any(_has_error(c) for c in node.children)


def parse_gate(content: str) -> GateResult:
    """tree-sitter must parse the new content with no ERROR/MISSING nodes."""
    tree = Parser(_PY_LANGUAGE).parse(content.encode())
    if _has_error(tree.root_node):
        return False, "syntax error: tree-sitter found ERROR/MISSING nodes"
    return True, "parsed clean"


def _ruff_violation_count(path: Path) -> int:
    out = subprocess.run(
        [_tool("ruff"), "check", "--output-format", "json", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return len(json.loads(out.stdout or "[]"))
    except json.JSONDecodeError:
        return 0


def lint_gate(path: Path, baseline_violations: int) -> GateResult:
    """Normalize formatting (behavior-preserving) then reject only *new* lint violations."""
    if _tool("ruff") is None:
        return None, "ruff not installed — skipped"
    subprocess.run([_tool("ruff"), "format", str(path)], capture_output=True, text=True)  # normalize
    new_count = _ruff_violation_count(path)
    if new_count > baseline_violations:
        return False, f"ruff: {new_count - baseline_violations} new violation(s)"
    return True, f"ruff clean ({new_count} <= {baseline_violations} baseline)"


def _pyright_error_count(path: Path) -> Optional[int]:
    """pyright error count for a file; None if output is unparseable. Assumes pyright installed."""
    out = subprocess.run(
        [_tool("pyright"), "--outputjson", str(path)], capture_output=True, text=True
    )
    try:
        return json.loads(out.stdout).get("summary", {}).get("errorCount", 0)
    except (json.JSONDecodeError, AttributeError):
        return None


def pyright_baseline(path: Path) -> int:
    """Type-error count on the original file, captured before the edit (0 if pyright absent)."""
    if _tool("pyright") is None:
        return 0
    count = _pyright_error_count(path)
    return count if count is not None else 0


def typecheck_gate(path: Path, baseline_errors: int = 0) -> GateResult:
    """Reject only *new* type errors vs the pre-edit baseline (mirrors lint_gate).

    A behavior-preserving refactor that leaves a pre-existing type complaint
    (e.g. a function that already returned ``int | None``) passes; only an edit
    that makes the type-error count go *up* is rolled back. Absolute "must be
    type-perfect" rejection over-rejects correct code, so we ask the same
    question as the lint gate: did this edit make types *worse*?
    """
    if _tool("pyright") is None:
        return None, "pyright not installed — skipped"
    count = _pyright_error_count(path)
    if count is None:
        return False, "pyright: unparseable output"
    if count > baseline_errors:
        return False, f"pyright: {count - baseline_errors} new type error(s)"
    return True, f"pyright clean ({count} <= {baseline_errors} baseline)"


def test_gate(repo_dir: Path) -> GateResult:
    """pytest over the repo. Exit 5 (no tests collected) -> skip; type-clean != correct."""
    if _tool("pytest") is None:
        return None, "pytest not installed — skipped"
    out = subprocess.run(
        [_tool("pytest"), "-q", "--no-header"], cwd=str(repo_dir), capture_output=True, text=True
    )
    if out.returncode == 5:
        return None, "no tests cover this file — skipped"
    if out.returncode != 0:
        tail = (out.stdout or out.stderr).strip().splitlines()[-1:] or ["tests failed"]
        return False, f"pytest failed: {tail[0]}"
    return True, "pytest green"


def ruff_baseline(path: Path) -> int:
    """Violation count on the original file, captured before the edit is written."""
    if _tool("ruff") is None:
        return 0
    return _ruff_violation_count(path)
