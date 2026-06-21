"""Deterministic transform engines — the only code that mutates source.

Each engine takes a `TransformSpec` and returns an `EditMap` (path -> new file
contents). Engines never write to disk or commit: the checker writes the EditMap,
runs the gate stack, and commits or reverts. This keeps every structural change
reference-correct (computed by rope/LibCST, not hand-written by an LLM) and atomic.
"""

from refactorika.transforms.base import EditMap, dispatch

__all__ = ["EditMap", "dispatch"]
