"""Call graph builder for dead-code reachability analysis.

Walks all *.py files in a directory (or a single file), builds a directed graph
of qualname -> set[qualname] edges, and exposes entry-point heuristics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from refactorika.analysis.parser import get_tree, iter_imports, iter_symbols

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKIP_DIRS = {
    ".venv",
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

_ENTRY_DECORATORS = {"app.route", "click.command", "pytest.fixture"}


def _module_name(file_path: Path, root: Path) -> str:
    """Derive dotted module name from file path relative to root."""
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        rel = file_path
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else file_path.stem


def _collect_py_files(path: str) -> tuple[list[Path], Path]:
    """Return (list of .py files to scan, root directory for module naming)."""
    p = Path(path)
    if p.is_file():
        return [p], p.parent
    files: list[Path] = []
    for f in p.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        files.append(f)
    return files, p


def _string_literal_text(node) -> Optional[str]:
    """Return the decoded inner text of a string node, or None if not a string."""
    if node.type != "string":
        return None
    parts: list[str] = []
    for child in node.children:
        if child.type == "string_content" and child.text:
            parts.append(child.text.decode())
    if parts:
        return "".join(parts)
    # Fallback: strip the surrounding quotes from the raw text.
    raw = node.text.decode() if node.text else ""
    return raw.strip("\"'")


def _parse_all_from_tree(tree) -> set[str]:
    """Collect names listed in a module-level ``__all__`` via the AST.

    Handles list **and** tuple (and set) literals, including multi-line ones —
    anything regex-over-source missed.
    """
    names: set[str] = set()
    root = tree.root_node
    for node in root.children:
        # __all__ = [...] / (...) is an expression_statement wrapping an assignment.
        assign = node
        if node.type == "expression_statement" and node.children:
            assign = node.children[0]
        if assign.type != "assignment":
            continue
        left = assign.child_by_field_name("left")
        right = assign.child_by_field_name("right")
        if left is None or right is None:
            continue
        if not (
            left.type == "identifier" and left.text and left.text.decode() == "__all__"
        ):
            continue
        if right.type not in ("list", "tuple", "set"):
            continue
        for elem in right.children:
            text = _string_literal_text(elem)
            if text and text.isidentifier():
                names.add(text)
    return names


def _find_main_block(tree):
    """Return the ``if __name__ == "__main__":`` if_statement node, or None."""
    root = tree.root_node
    for node in root.children:
        if node.type != "if_statement":
            continue
        cond = node.child_by_field_name("condition")
        if cond is None or cond.type != "comparison_operator":
            continue
        cond_text = cond.text.decode() if cond.text else ""
        # Normalize quotes/spacing: __name__ == "__main__" or '__main__'.
        normalized = cond_text.replace(" ", "")
        if "__name__==" in normalized and "__main__" in normalized:
            return node
    return None


def _has_main_block(tree) -> bool:
    return _find_main_block(tree) is not None


def _main_block_calls(tree) -> set[str]:
    """Extract function names called anywhere inside the ``__main__`` block.

    Walks the full block subtree (multi-line and nested calls included) via the
    AST instead of a single-line regex.
    """
    block = _find_main_block(tree)
    if block is None:
        return set()
    return set(_iter_calls_from_node(block))


def _decorator_texts(node) -> list[str]:
    """Return decorator expression texts for a function/class node."""
    decorators: list[str] = []
    for child in node.children:
        if child.type == "decorator":
            # decorator -> '@' followed by the expression
            text = child.text.decode() if child.text else ""
            text = text.lstrip("@").strip()
            decorators.append(text)
    return decorators


# ---------------------------------------------------------------------------
# CallGraph
# ---------------------------------------------------------------------------


class CallGraph:
    """Directed call graph over all symbols in a Python project."""

    def __init__(self) -> None:
        # qualname -> (kind, file_path_str, line)
        self._nodes: dict[str, tuple[str, str, int]] = {}
        # qualname -> set of qualnames it calls
        self._edges: dict[str, set[str]] = {}
        # qualnames considered entry points
        self._entry_points: set[str] = set()

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------

    @classmethod
    def build(cls, path: str) -> "CallGraph":
        """Parse all *.py files under *path* and construct the call graph."""
        cg = cls()
        files, root = _collect_py_files(path)

        # Pass 1: collect all symbols and build per-file data needed for edge resolution.
        # per_file: module -> { local_name -> qualname,  import_alias -> qualname }
        per_file_symbols: dict[str, dict[str, str]] = {}  # module -> {name: qualname}
        per_file_imports: dict[str, dict[str, str]] = {}  # module -> {alias: qualname}
        file_trees: dict[str, object] = {}  # module -> tree
        file_paths: dict[str, str] = {}  # module -> filesystem path

        for fpath in files:
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
                tree = get_tree(source)
            except Exception:
                continue

            module = _module_name(fpath, root)
            file_trees[module] = tree
            file_paths[module] = str(fpath)

            sym_map: dict[str, str] = {}
            for node, kind, name, line in iter_symbols(tree):
                qualname = f"{module}.{name}"
                cg._nodes[qualname] = (kind, str(fpath), line)
                sym_map[name] = qualname
            per_file_symbols[module] = sym_map

        # Pass 1b: collect import aliases per module
        for module, tree in file_trees.items():
            import_map: dict[str, str] = {}
            try:
                for mod, names in iter_imports(tree):
                    if names:
                        for nm in names:
                            # e.g. "from orders import compute_total" -> compute_total: orders.compute_total
                            import_map[nm] = f"{mod}.{nm}"
                    else:
                        # bare "import foo" -> foo: foo
                        top = mod.split(".")[0]
                        import_map[top] = mod
            except Exception:
                pass
            per_file_imports[module] = import_map

        # Build a project-wide unqualified-name -> qualname map, but ONLY for
        # names that are unique across the whole project. Ambiguous names (e.g.
        # two modules each defining `compute`) are deliberately excluded so a
        # bare call to an ambiguous name resolves to no edge instead of guessing.
        _unq_counts: dict[str, list[str]] = {}
        for qualname in cg._nodes:
            _unq_counts.setdefault(qualname.split(".")[-1], []).append(qualname)
        unique_by_unqualified: dict[str, str] = {
            unq: quals[0] for unq, quals in _unq_counts.items() if len(quals) == 1
        }

        # Pass 2: build edges + detect entry points
        for module, tree in file_trees.items():
            sym_map = per_file_symbols.get(module, {})
            import_map = per_file_imports.get(module, {})

            all_dunder_names = _parse_all_from_tree(tree)
            main_calls = _main_block_calls(tree)
            is_test_file = Path(file_paths[module]).name.startswith("test_") or Path(
                file_paths[module]
            ).name.endswith("_test.py")

            for node, kind, name, line in iter_symbols(tree):
                qualname = f"{module}.{name}"

                # Determine entry point
                is_entry = False

                # Public name -> conservative entry point
                if not name.startswith("_"):
                    is_entry = True

                # __all__ inclusion
                if name in all_dunder_names:
                    is_entry = True

                # inside __main__ block call
                if name in main_calls:
                    is_entry = True

                # test_ prefix or in test file
                if name.startswith("test_") or is_test_file:
                    is_entry = True

                # decorator heuristic
                for deco_text in _decorator_texts(node):
                    for ep_deco in _ENTRY_DECORATORS:
                        if deco_text.startswith(ep_deco):
                            is_entry = True
                            break

                if is_entry:
                    cg._entry_points.add(qualname)

                # Build edges: collect call names from this node's body
                try:
                    # iter_calls walks the whole tree; we scope it to this node
                    sub_tree_calls = list(_iter_calls_from_node(node))
                except Exception:
                    sub_tree_calls = []

                edge_set: set[str] = set()
                for call_name in sub_tree_calls:
                    resolved = _resolve_name(
                        call_name,
                        module,
                        sym_map,
                        import_map,
                        cg._nodes,
                        unique_by_unqualified,
                    )
                    if resolved:
                        edge_set.add(resolved)

                cg._edges.setdefault(qualname, set()).update(edge_set)

        # Ensure every node has an (possibly empty) edge set
        for qualname in cg._nodes:
            cg._edges.setdefault(qualname, set())

        return cg

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call_sites(self, name: str) -> int:
        """Count how many edges point TO *name* (exact qualname only).

        Edges store fully-resolved qualnames, so an exact match is the correct
        count. We deliberately do **not** match on the unqualified suffix —
        doing so would credit calls aimed at a *different* same-named symbol in
        another module, inflating the count and masking genuinely-dead code.
        """
        count = 0
        for targets in self._edges.values():
            if name in targets:
                count += 1
        return count

    def edges_from(self, qualname: str) -> set[str]:
        """Outbound references (qualnames) from *qualname*."""
        return self._edges.get(qualname, set())

    def all_symbols(self) -> set[str]:
        """All known qualnames."""
        return set(self._nodes.keys())

    def dependents_of(self, module: str) -> list[str]:
        """Modules referencing *module* (matched by final segment) via call-graph edges."""
        target = module.split(".")[-1]
        dependents: set[str] = set()
        for qualname in self.all_symbols():
            src_module = qualname.rsplit(".", 1)[0] if "." in qualname else qualname
            if src_module.split(".")[-1] == target:
                continue  # references within the same module aren't "dependents"
            for t in self.edges_from(qualname):
                t_module = t.rsplit(".", 1)[0] if "." in t else t
                if t_module.split(".")[-1] == target:
                    dependents.add(src_module)
                    break
        return sorted(dependents)

    def dependent_count(self, module: str) -> int:
        """How many other modules depend on *module* (blast radius)."""
        return len(self.dependents_of(module))

    def entry_points(self) -> set[str]:
        """Conservatively reachable anchors."""
        return set(self._entry_points)

    def node_info(self, qualname: str) -> Optional[tuple[str, str, int]]:
        """Return (kind, file, line) for a qualname, or None if unknown."""
        return self._nodes.get(qualname)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iter_calls_from_node(node) -> list[str]:
    """Collect call target names from a single AST node (and its descendants)."""
    results: list[str] = []

    def _walk(n) -> None:
        if n.type == "call":
            fn = n.child_by_field_name("function")
            if fn is not None:
                if fn.type == "identifier" and fn.text:
                    results.append(fn.text.decode())
                elif fn.type == "attribute":
                    attr = fn.child_by_field_name("attribute")
                    if attr is not None and attr.text:
                        results.append(attr.text.decode())
        for child in n.children:
            _walk(child)

    _walk(node)
    return results


def _resolve_name(
    name: str,
    current_module: str,
    sym_map: dict[str, str],
    import_map: dict[str, str],
    all_nodes: dict[str, tuple],
    unique_by_unqualified: dict[str, str],
) -> Optional[str]:
    """Resolve a bare call name to a fully qualified name, or None.

    Resolution is *scoped* — we never credit a call to an arbitrary same-named
    symbol in another module (that invents false edges and makes genuinely-dead
    code look alive). Order:

    1. Same-module symbol table.
    2. Real imported-name map (the name was explicitly imported into this module).
    3. A project-wide unqualified-name match **only when it is unambiguous**
       (exactly one symbol anywhere bears that unqualified name). When the name
       is ambiguous across modules, we record **no edge** rather than guessing.
    """
    # 1. Same-module symbol
    if name in sym_map:
        return sym_map[name]

    # 2. Imported alias -> the real target it was imported as
    if name in import_map:
        candidate = import_map[name]
        if candidate in all_nodes:
            return candidate

    # 3. Unambiguous project-wide match (one and only one symbol has this name).
    #    Ambiguous names resolve to None -> no edge.
    return unique_by_unqualified.get(name)
