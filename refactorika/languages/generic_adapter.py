"""Generic fallback adapter — skips all gates; accepts any single file."""

from __future__ import annotations

from pathlib import Path

from .base import LanguageAdapter, _COMMON_SKIP


class GenericAdapter(LanguageAdapter):
    name = "Generic"
    extensions = frozenset()  # matches nothing in the registry; used as fallback only

    def collect_files(self, path: Path, skip_dirs: frozenset[str] = _COMMON_SKIP) -> list[Path]:
        # Accept a single file of any type; skip directory walks (no known extension pattern).
        if path.is_file():
            return [path]
        return []
