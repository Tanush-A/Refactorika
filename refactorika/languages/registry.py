"""Central language registry — maps file extensions to LanguageAdapters."""

from __future__ import annotations

from pathlib import Path

from .base import LanguageAdapter

_registry: dict[str, LanguageAdapter] = {}
_generic: LanguageAdapter | None = None


def register_adapter(adapter: LanguageAdapter, *, generic: bool = False) -> None:
    if generic:
        global _generic
        _generic = adapter
        return
    for ext in adapter.extensions:
        _registry[ext.lower()] = adapter


def detect_language(path: str | Path) -> LanguageAdapter:
    ext = Path(path).suffix.lower()
    result = _registry.get(ext)
    if result is not None:
        return result
    if _generic is not None:
        return _generic
    raise RuntimeError("No language adapter registered and no generic fallback set.")
