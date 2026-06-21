"""Shared types and dispatch for the deterministic transform engines.

`EditMap` is the universal output: a mapping of absolute file path -> new contents.
An engine returning ``{}`` means "no change" (the checker treats it as a no-op).
`dispatch` routes a `TransformSpec` to the engine that knows its `kind`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from refactorika.core.schema import TransformSpec
    from refactorika.graph.model import Graph

# Absolute file path -> new file contents. The checker writes these atomically.
EditMap = dict[str, str]


def line_col_to_offset(source: str, line: int, col: int) -> int:
    """Byte/char offset into *source* for a 1-based line and 0-based column."""
    lines = source.splitlines(keepends=True)
    return sum(len(ln) for ln in lines[: line - 1]) + col


def dispatch(spec: "TransformSpec", root: str, graph: "Graph") -> EditMap:
    """Apply *spec* via the engine registered for its kind. Returns an EditMap.

    Imports are local so that importing this module doesn't pull rope/LibCST unless
    a transform actually runs.
    """
    kind = spec.kind
    if kind == "rename":
        from refactorika.transforms.rename import rename

        return rename(spec, root, graph)
    if kind == "cleanup":
        from refactorika.transforms.cleanup import cleanup

        return cleanup(spec, root, graph)
    if kind == "remove_dead_code":
        from refactorika.transforms.dead_code import remove_dead_code

        return remove_dead_code(spec, root, graph)
    if kind in ("decompose_function", "extract", "inline", "change_signature", "move"):
        from refactorika.transforms.node_replace import replace_function

        # These local rewrites all land as a function-body replacement for v1; the
        # cross-file variants (move/change_signature) extend this in later passes.
        return replace_function(spec, root, graph)
    raise ValueError(f"no transform engine for kind={kind!r}")


_ENGINES: dict[str, Callable] = {}  # reserved for plugin registration
