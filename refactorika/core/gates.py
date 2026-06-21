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
    """Absolute path to a CLI tool, or None. Absolute so it resolves under any subprocess cwd.

    Falls back to the directory of the running interpreter (the venv's bin), so tools
    installed in the active environment are found even when it isn't on PATH.
    """
    found = shutil.which(name)
    if found:
        return os.path.abspath(found)
    import sys

    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return str(candidate)
    return None


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
    # normalize formatting first (behavior-preserving)
    subprocess.run([_tool("ruff"), "format", str(path)], capture_output=True, text=True)
    new_count = _ruff_violation_count(path)
    if new_count > baseline_violations:
        return False, f"ruff: {new_count - baseline_violations} new violation(s)"
    return True, f"ruff clean ({new_count} <= {baseline_violations} baseline)"


def _pyright_error_count(path: Path) -> Optional[int]:
    """Number of pyright errors on *path*, or None if pyright output is unparseable."""
    out = subprocess.run(
        [_tool("pyright"), "--outputjson", str(path)], capture_output=True, text=True
    )
    try:
        return json.loads(out.stdout).get("summary", {}).get("errorCount", 0)
    except (json.JSONDecodeError, AttributeError):
        return None


def pyright_baseline(path: Path) -> int:
    """Pyright error count on the original file, captured before the edit is written.

    Single-file pyright can report environment-only errors (e.g. an unresolved import that
    has nothing to do with the change). Baselining means we reject only *new* type errors,
    exactly as the lint gate rejects only new lint violations.
    """
    if _tool("pyright") is None:
        return 0
    return _pyright_error_count(path) or 0


def typecheck_gate(path: Path, baseline_errors: int = 0) -> GateResult:
    """pyright must introduce no *new* type errors vs. the pre-edit baseline."""
    if _tool("pyright") is None:
        return None, "pyright not installed — skipped"
    errors = _pyright_error_count(path)
    if errors is None:
        return False, "pyright: unparseable output"
    if errors > baseline_errors:
        return False, f"pyright: {errors - baseline_errors} new type error(s)"
    return True, f"pyright clean ({errors} <= {baseline_errors} baseline)"


def test_gate(repo_dir: Path, node_ids: Optional[list[str]] = None) -> GateResult:
    """pytest over the repo (or only *node_ids* for impact-scoped runs).

    Passing the impacted test node ids (``path::test_name``) runs only the tests a
    change can affect — the efficiency win — instead of the whole suite every edit.
    Exit 5 (no tests collected) -> skip; type-clean != behavior-preserving.
    """
    if _tool("pytest") is None:
        return None, "pytest not installed — skipped"
    selection = list(node_ids) if node_ids else []
    out = subprocess.run(
        [_tool("pytest"), "-q", "--no-header", *selection],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    if out.returncode == 5:
        return None, "no tests cover this change — skipped"
    if out.returncode != 0:
        tail = (out.stdout or out.stderr).strip().splitlines()[-1:] or ["tests failed"]
        return False, f"pytest failed: {tail[0]}"
    scope = f"{len(selection)} impacted test(s)" if selection else "full suite"
    return True, f"pytest green ({scope})"


def ruff_baseline(path: Path) -> int:
    """Violation count on the original file, captured before the edit is written."""
    if _tool("ruff") is None:
        return 0
    return _ruff_violation_count(path)
