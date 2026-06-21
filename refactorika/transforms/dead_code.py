"""Dead-code removal via LibCST — delete one flagged symbol, losslessly.

Removes a single top-level function/class/assignment by name from its module, using
LibCST so formatting and comments elsewhere are preserved exactly. One symbol per call
keeps the change atomic and gateable; the orchestrator re-runs reachability after each
removal to drive the cascade (a removed function can orphan its helper, then a constant).
"""

from __future__ import annotations

from pathlib import Path

import libcst as cst

from refactorika.core.schema import TransformSpec
from refactorika.graph.model import Graph
from refactorika.transforms.base import EditMap


def _defines_name(stmt: cst.CSTNode, name: str) -> bool:
    """True if a top-level statement defines *name* (def/class/assignment)."""
    if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)):
        return stmt.name.value == name
    if isinstance(stmt, cst.SimpleStatementLine):
        for small in stmt.body:
            if isinstance(small, cst.Assign):
                for tgt in small.targets:
                    if isinstance(tgt.target, cst.Name) and tgt.target.value == name:
                        return True
            if isinstance(small, cst.AnnAssign) and isinstance(small.target, cst.Name):
                if small.target.value == name:
                    return True
    return False


def remove_symbol_from_source(source: str, name: str) -> str:
    """Return *source* with the top-level definition of *name* removed."""
    module = cst.parse_module(source)
    new_body = [stmt for stmt in module.body if not _defines_name(stmt, name)]
    if len(new_body) == len(module.body):
        return source  # nothing matched; no-op
    return module.with_changes(body=new_body).code


def remove_dead_code(spec: TransformSpec, root: str, graph: Graph) -> EditMap:
    """Remove the dead symbol ``spec.target`` from its file."""
    sym = graph.symbols.get(spec.target)
    if sym is None:
        raise ValueError(f"remove_dead_code target not in graph: {spec.target}")
    p = Path(sym.file).resolve()
    original = p.read_text(encoding="utf-8")
    updated = remove_symbol_from_source(original, sym.name)
    if updated == original:
        return {}
    return {str(p): updated}
