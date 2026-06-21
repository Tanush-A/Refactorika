"""Reference-correct symbol move — file restructuring (create a module, move code into it).

Moves a top-level function/class to another module (existing or newly created) via rope, which
rewrites the definition into the destination and updates every import/reference across the repo.
Like the other engines it returns an EditMap without leaving side effects: a destination file it
had to create is removed again before returning (its final contents are in the EditMap, so the
checker creates it atomically and can delete it on rollback).
"""

from __future__ import annotations

import os
from pathlib import Path

from rope.base.project import Project
from rope.refactor import move

from refactorika.core.schema import TransformSpec
from refactorika.graph.model import Graph
from refactorika.transforms.base import EditMap, line_col_to_offset


def _module_to_path(root: Path, dotted: str) -> Path:
    return root / (Path(*dotted.split(".")).with_suffix(".py"))


def move_symbol(spec: TransformSpec, root: str, graph: Graph) -> EditMap:
    """Move ``spec.target`` to ``spec.params['dest_module']`` (created if needed), repo-wide."""
    sym = graph.symbols.get(spec.target)
    if sym is None:
        raise ValueError(f"move target not in graph: {spec.target}")
    dest_module = spec.params.get("dest_module")
    if not dest_module:
        raise ValueError("move requires params['dest_module']")

    root_abs = Path(root).resolve()
    dest_path = _module_to_path(root_abs, dest_module)
    created = not dest_path.exists()
    if created:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text("")  # rope needs the destination resource to exist

    project = Project(
        str(root_abs), ropefolder=None, ignore_syntax_errors=True, ignore_bad_imports=True
    )
    try:
        project.validate(project.root)
        src_rel = os.path.relpath(str(Path(sym.file).resolve()), root_abs).replace(os.sep, "/")
        dest_rel = os.path.relpath(str(dest_path.resolve()), root_abs).replace(os.sep, "/")
        src_res = project.get_resource(src_rel)
        offset = line_col_to_offset(src_res.read(), sym.line, sym.column)
        mover = move.create_move(project, src_res, offset)
        changes = mover.get_changes(project.get_resource(dest_rel))

        edits: EditMap = {}
        for change in changes.changes:
            new_contents = getattr(change, "new_contents", None)
            res = getattr(change, "resource", None)
            if new_contents is None or res is None:
                continue
            edits[str(root_abs / res.path)] = new_contents
        # ensure the destination is represented even if rope reported it as a creation
        if str(dest_path) not in edits and dest_path.exists():
            edits[str(dest_path)] = dest_path.read_text() if not created else ""
        return edits
    finally:
        project.close()
        if created and dest_path.exists():
            dest_path.unlink()  # leave no trace; the checker re-creates it from the EditMap
