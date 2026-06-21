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
    return rename_at(root, sym.file, offset, new_name)


def rename_at(root: str, file: str, offset: int, new_name: str) -> EditMap:
    """Reference-correct rename of the symbol at *offset* in *file*, repo-wide.

    Lower-level entry point: works from a (file, offset) without needing a prebuilt graph,
    so callers operating on large repos (e.g. the eval) can locate a definition cheaply and
    rename it. Returns the edited file contents without touching disk.
    """
    root_abs = str(Path(root).resolve())
    # ropefolder=None: no on-disk cache; get_changes never writes. ignore_syntax_errors keeps
    # rope from crashing on real repos that ship intentionally-broken files (e.g. Django's
    # test fixtures); ignore_bad_imports tolerates unresolved third-party imports.
    project = Project(
        root_abs, ropefolder=None, ignore_syntax_errors=True, ignore_bad_imports=True
    )
    try:
        rel = os.path.relpath(str(Path(file).resolve()), root_abs)
        resource = project.get_resource(rel.replace(os.sep, "/"))
        changes = Rename(project, resource, offset).get_changes(new_name)
        edits: EditMap = {}
        for change in changes.changes:
            new_contents = getattr(change, "new_contents", None)
            res = getattr(change, "resource", None)
            if new_contents is None or res is None:
                continue
            edits[str(Path(root_abs) / res.path)] = new_contents
        return edits
    finally:
        project.close()
