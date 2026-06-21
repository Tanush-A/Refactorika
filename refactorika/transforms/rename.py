"""Reference-correct cross-file rename-propagation — the centerpiece engine.

Renaming a symbol must update its definition *and every reference* — call sites,
imports, re-exports — and nothing that merely shares the name. rope does this with
real binding analysis; we extract the resulting file contents from its changeset
*without applying them to disk*, so the checker stays in control of commit/rollback.
"""

from __future__ import annotations

import os
from pathlib import Path

from rope.base.project import Project
from rope.refactor.rename import Rename

from refactorika.core.schema import TransformSpec
from refactorika.graph.model import Graph
from refactorika.transforms.base import EditMap, line_col_to_offset


def rename(spec: TransformSpec, root: str, graph: Graph) -> EditMap:
    """Rename ``spec.target`` to ``spec.params['new_name']`` across the whole repo."""
    sym = graph.symbols.get(spec.target)
    if sym is None:
        raise ValueError(f"rename target not in graph: {spec.target}")
    new_name = spec.params.get("new_name")
    if not new_name:
        raise ValueError("rename requires params['new_name']")
    if new_name == sym.name:
        return {}

    source = Path(sym.file).read_text(encoding="utf-8")
    offset = line_col_to_offset(source, sym.line, sym.column)

    root_abs = str(Path(root).resolve())
    # ropefolder=None: no on-disk cache; get_changes() never writes.
    project = Project(root_abs, ropefolder=None)
    try:
        rel = os.path.relpath(str(Path(sym.file).resolve()), root_abs)
        resource = project.get_resource(rel.replace(os.sep, "/"))
        changes = Rename(project, resource, offset).get_changes(new_name)
        edits: EditMap = {}
        for change in changes.changes:
            new_contents = getattr(change, "new_contents", None)
            res = getattr(change, "resource", None)
            if new_contents is None or res is None:
                continue
            abspath = str(Path(root_abs) / res.path)
            edits[abspath] = new_contents
        return edits
    finally:
        project.close()
