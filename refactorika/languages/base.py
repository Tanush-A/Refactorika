"""LanguageAdapter ABC — encapsulates all language-specific behavior."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

GateResult = tuple[Optional[bool], str]

_COMMON_SKIP = frozenset({".git", "__pycache__", ".venv", "node_modules", "dist"})


class LanguageAdapter(ABC):
    """One instance per language; registered in the central registry."""

    name: str
    extensions: frozenset[str]

    # --- Parse gate ---

    def parse_gate(self, content: str) -> GateResult:
        return None, f"{self.name}: no parser available — skipped"

    # --- Lint baseline + gate ---

    def lint_baseline(self, path: Path) -> int:
        return 0

    def lint_gate(self, path: Path, baseline: int) -> GateResult:
        return None, f"{self.name}: no linter available — skipped"

    # --- Typecheck baseline + gate ---

    def typecheck_baseline(self, path: Path) -> int:
        return 0

    def typecheck_gate(self, path: Path, baseline: int) -> GateResult:
        return None, f"{self.name}: no typechecker available — skipped"

    # --- File collection ---

    @abstractmethod
    def collect_files(self, path: Path, skip_dirs: frozenset[str] = _COMMON_SKIP) -> list[Path]:
        """Return all source files for this language under path."""
