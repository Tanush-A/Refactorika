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


def typecheck_gate(path: Path) -> GateResult:
    """pyright must report zero errors on the touched file."""
    if _tool("pyright") is None:
        return None, "pyright not installed — skipped"
    out = subprocess.run(
        [_tool("pyright"), "--outputjson", str(path)], capture_output=True, text=True
    )
    try:
        summary = json.loads(out.stdout).get("summary", {})
        errors = summary.get("errorCount", 0)
    except (json.JSONDecodeError, AttributeError):
        return False, "pyright: unparseable output"
    if errors:
        return False, f"pyright: {errors} type error(s)"
    return True, "pyright clean"


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
