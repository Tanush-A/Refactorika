"""Python LanguageAdapter — delegates to existing gates.py functions."""

from __future__ import annotations

from pathlib import Path

from refactorika.core.gates import (
    GateResult,
    lint_gate as _lint_gate,
    parse_gate as _parse_gate,
    pyright_baseline,
    ruff_baseline,
    typecheck_gate as _typecheck_gate,
)

from .base import LanguageAdapter

_SKIP = frozenset({
    ".venv", "__pycache__", ".git", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "node_modules",
})


class PythonAdapter(LanguageAdapter):
    name = "Python"
    extensions = frozenset({".py"})

    def parse_gate(self, content: str) -> GateResult:
        return _parse_gate(content)

    def lint_baseline(self, path: Path) -> int:
        return ruff_baseline(path)

    def lint_gate(self, path: Path, baseline: int) -> GateResult:
        return _lint_gate(path, baseline)

    def typecheck_baseline(self, path: Path) -> int:
        return pyright_baseline(path)

    def typecheck_gate(self, path: Path, baseline: int) -> GateResult:
        return _typecheck_gate(path, baseline)

    def collect_files(self, path: Path, skip_dirs: frozenset[str] = _SKIP) -> list[Path]:
        if path.is_file():
            return [path] if path.suffix == ".py" else []
        return [
            f for f in path.rglob("*.py")
            if not any(part in skip_dirs for part in f.parts)
        ]
