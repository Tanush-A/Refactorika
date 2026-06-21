"""AST-node replacement via LibCST — apply a local rewrite without raw diffs.

The LLM supplies new source for a function (e.g. a god function decomposed into named
helpers). We parse it and swap the matching top-level ``FunctionDef`` node for the new
statements, so application can never half-apply or drift the way a raw unified diff can.
The replacement is then gated like any other change.
"""

from __future__ import annotations

from pathlib import Path

import libcst as cst

from refactorika.core.schema import TransformSpec
from refactorika.graph.model import Graph
from refactorika.transforms.base import EditMap


def replace_function_in_source(source: str, name: str, new_source: str) -> str:
    """Replace the top-level function *name* in *source* with parsed *new_source*.

    *new_source* may define more than one statement (the decomposition: a helper plus
    the slimmed original), which are spliced in where the original function stood.
    """
    module = cst.parse_module(source)
    replacement = list(cst.parse_module(new_source).body)

    new_body: list[cst.CSTNode] = []
    replaced = False
    for stmt in module.body:
        if isinstance(stmt, cst.FunctionDef) and stmt.name.value == name:
            new_body.extend(replacement)
            replaced = True
        else:
            new_body.append(stmt)
    if not replaced:
        return source
    return module.with_changes(body=new_body).code


def replace_function(spec: TransformSpec, root: str, graph: Graph) -> EditMap:
    """Replace ``spec.target``'s body with ``spec.params['new_source']``."""
    sym = graph.symbols.get(spec.target)
    if sym is None:
        raise ValueError(f"replace_function target not in graph: {spec.target}")
    new_source = spec.params.get("new_source")
    if not new_source:
        raise ValueError("replace_function requires params['new_source']")
    p = Path(sym.file).resolve()
    original = p.read_text(encoding="utf-8")
    updated = replace_function_in_source(original, sym.name, new_source)
    if updated == original:
        return {}
    return {str(p): updated}
