"""Call graph builder for dead-code reachability analysis.

Walks all *.py files in a directory (or a single file), builds a directed graph
of qualname -> set[qualname] edges, and exposes entry-point heuristics.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from refactorika.analysis.parser import get_tree, iter_symbols, iter_imports


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".venv", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

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


def _scan_all_names_in_source(source: str) -> set[str]:
    """Return every bare word that appears in string literals in the source."""
    names: set[str] = set()
    for m in re.finditer(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']', source):
        names.add(m.group(1))
    return names


def _parse_all_in_source(source: str) -> set[str]:
    """Collect names that appear in __all__ list literals."""
    names: set[str] = set()
    m = re.search(r"__all__\s*=\s*\[([^\]]*)\]", source)
    if m:
        for nm in re.findall(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']', m.group(1)):
            names.add(nm)
    return names


def _has_main_block(source: str) -> bool:
    return bool(re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', source))


def _main_block_calls(source: str) -> set[str]:
    """Heuristically extract function names called in the __main__ block."""
    names: set[str] = set()
    m = re.search(
        r'if\s+__name__\s*==\s*["\']__main__["\']\s*:(.*)',
        source,
        re.DOTALL,
    )
    if m:
        block = m.group(1)
        for call in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", block):
            names.add(call)
    return names


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
        file_sources: dict[str, str] = {}  # module -> source text
        file_trees: dict[str, object] = {}  # module -> tree
        file_paths: dict[str, str] = {}  # module -> filesystem path

        for fpath in files:
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
                tree = get_tree(source)
            except Exception:
                continue

            module = _module_name(fpath, root)
            file_sources[module] = source
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

        # Pass 2: build edges + detect entry points
        for module, tree in file_trees.items():
            source = file_sources[module]
            sym_map = per_file_symbols.get(module, {})
            import_map = per_file_imports.get(module, {})

            all_dunder_names = _parse_all_in_source(source)
            main_calls = _main_block_calls(source)
            is_test_file = (
                Path(file_paths[module]).name.startswith("test_")
                or Path(file_paths[module]).name.endswith("_test.py")
            )

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
                    resolved = _resolve_name(call_name, module, sym_map, import_map, cg._nodes)
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
        """Count how many edges point TO *name* (exact qualname or any *.name suffix)."""
        count = 0
        unqualified = name.split(".")[-1]
        for src, targets in self._edges.items():
            for t in targets:
                if t == name or t.split(".")[-1] == unqualified:
                    count += 1
        return count

    def edges_from(self, qualname: str) -> set[str]:
        """Outbound references (qualnames) from *qualname*."""
        return self._edges.get(qualname, set())

    def all_symbols(self) -> set[str]:
        """All known qualnames."""
        return set(self._nodes.keys())

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
) -> Optional[str]:
    """Try to resolve a bare call name to a fully qualified name."""
    # 1. Same-module symbol
    if name in sym_map:
        return sym_map[name]

    # 2. Imported alias
    if name in import_map:
        candidate = import_map[name]
        if candidate in all_nodes:
            return candidate

    # 3. Any node whose unqualified name matches
    # (catches cross-module calls we couldn't fully resolve via imports)
    for qualname in all_nodes:
        if qualname.split(".")[-1] == name:
            return qualname

    return None
