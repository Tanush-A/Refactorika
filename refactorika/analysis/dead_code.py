"""Dead-code detection via call-graph reachability.

BFS/DFS from entry points; anything not reachable is a dead-code candidate.
Confidence is assigned based on naming conventions and string-literal reflection risk.
"""

from __future__ import annotations

import hashlib

from refactorika.analysis.call_graph import CallGraph, _collect_py_files
from refactorika.analysis.parser import get_tree
from refactorika.core.schema import DeadSymbol
from refactorika.core.storage import Storage

# Builtins that take a string attribute/key name and dynamically resolve a symbol.
_REFLECTION_FUNCS = {"getattr", "setattr", "hasattr", "delattr"}


def find_dead_code(path: str, storage: Storage) -> dict:
    """Detect unreachable symbols in *path* via call-graph reachability.

    Parameters
    ----------
    path:
        File or directory to analyse.
    storage:
        Storage instance. Used to cache the result on an AST/content signature
        of the analysed files (Redis primary, JSON fallback) so a re-run over an
        unchanged tree skips the whole call-graph build.

    Returns
    -------
    dict with keys:
        "path"          - the analysed path
        "entry_points"  - list of qualnames used as BFS roots
        "dead_symbols"  - list of DeadSymbol.to_dict() sorted by rank descending
    """
    # Cache on a signature of every analysed file (path + content). A re-seen,
    # unchanged tree returns the prior result without re-parsing.
    cache_key = _dir_signature(path)
    if cache_key is not None:
        cached = storage.cache_get(cache_key)
        if cached is not None:
            return cached

    # Build call graph
    try:
        call_graph = CallGraph.build(path)
    except Exception as exc:
        return {
            "path": path,
            "entry_points": [],
            "dead_symbols": [],
            "error": str(exc),
        }

    all_symbols = call_graph.all_symbols()
    entry_pts = call_graph.entry_points()

    # BFS/DFS reachability from entry points
    reachable: set[str] = set()
    frontier = list(entry_pts & all_symbols)
    while frontier:
        node = frontier.pop()
        if node in reachable:
            continue
        reachable.add(node)
        for child in call_graph.edges_from(node):
            if child not in reachable:
                frontier.append(child)

    # Collect names that appear in *actual* reflection / dynamic-dispatch
    # patterns (getattr("name"), string dispatch-dict keys) — not every string.
    reflection_names = _collect_reflection_names(path)

    # Identify dead symbols
    dead: list[DeadSymbol] = []
    for qualname in all_symbols:
        if qualname in reachable:
            continue

        info = call_graph.node_info(qualname)
        if info is None:
            continue
        kind, file_str, line = info

        unqualified = qualname.split(".")[-1]
        sites = call_graph.call_sites(qualname)

        # Assign confidence + reason.
        # Reflection wins over everything: a name resolved dynamically
        # (getattr / dispatch-dict key) can't be trusted as dead — flag low,
        # even for a private name with zero static call sites.
        if unqualified in reflection_names:
            confidence = "low"
            rank = 30
            reason = (
                f"Name '{unqualified}' has {sites} static call site(s) but appears as a "
                "reflection/dispatch string (getattr/dispatch key) — possible dynamic usage."
            )
        elif unqualified.startswith("_") and sites == 0:
            confidence = "high"
            rank = 90
            reason = f"Private name '{unqualified}' with zero call sites and unreachable from entry points."
        elif sites == 0:
            confidence = "medium"
            rank = 60
            reason = (
                f"Public name '{unqualified}' has zero call sites within the analysed codebase "
                "and is unreachable from entry points."
            )
        else:
            # Has call sites but still unreachable — unusual; treat as medium.
            confidence = "medium"
            rank = 60
            reason = (
                f"Symbol '{unqualified}' is unreachable from entry points "
                f"(call_sites={sites})."
            )

        dead.append(
            DeadSymbol(
                kind=kind,
                name=qualname,
                file=file_str,
                line=line,
                confidence=confidence,
                reason=reason,
                rank=rank,
            )
        )

    # Sort by rank descending (highest confidence first)
    dead.sort(key=lambda d: d.rank, reverse=True)

    result = {
        "path": path,
        "entry_points": sorted(entry_pts),
        "dead_symbols": [d.to_dict() for d in dead],
    }
    if cache_key is not None:
        storage.cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dir_signature(path: str) -> str | None:
    """Sha1 over every analysed file's relative path + content.

    Returns ``None`` if no files are readable (nothing to cache on). Sorting the
    inputs keeps the signature stable regardless of filesystem walk order.
    """
    files, root = _collect_py_files(path)
    items: list[str] = []
    for fpath in sorted(files):
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            rel = str(fpath.relative_to(root))
        except ValueError:
            rel = str(fpath)
        items.append(f"{rel}\0{content}")
    if not items:
        return None
    digest = hashlib.sha1("\0\0".join(items).encode()).hexdigest()
    return f"dead_code:{digest}"


def _collect_reflection_names(path: str) -> set[str]:
    """Return identifiers that appear in *actual reflection / dynamic-dispatch* sites.

    Narrow on purpose (the old version matched any identifier-like substring in
    any string/comment, which demoted far too many symbols to ``low``). We only
    collect a name when it is used in a way that could dynamically resolve a
    symbol:

    * a string-literal argument to ``getattr`` / ``setattr`` / ``hasattr`` /
      ``delattr`` (e.g. ``getattr(obj, "handle_event")``);
    * a string-literal key in a dict literal — a dispatch table
      (e.g. ``{"create": create_user, "delete": delete_user}``).
    """
    names: set[str] = set()
    files, _ = _collect_py_files(path)
    for fpath in files:
        try:
            source = fpath.read_text(encoding="utf-8", errors="replace")
            tree = get_tree(source)
        except Exception:
            continue
        _walk_reflection(tree.root_node, names)
    return names


def _walk_reflection(node, names: set[str]) -> None:
    """Recursively collect reflection/dispatch string names from a subtree."""
    if node.type == "call":
        _collect_reflection_call(node, names)
    elif node.type == "dictionary":
        for child in node.children:
            if child.type == "pair":
                key = child.child_by_field_name("key")
                text = _string_identifier(key) if key is not None else None
                if text:
                    names.add(text)
    for child in node.children:
        _walk_reflection(child, names)


def _collect_reflection_call(call_node, names: set[str]) -> None:
    """If *call_node* is getattr/setattr/..., collect its string-literal name args."""
    fn = call_node.child_by_field_name("function")
    fn_name = (
        fn.text.decode()
        if (fn is not None and fn.type == "identifier" and fn.text)
        else ""
    )
    if fn_name not in _REFLECTION_FUNCS:
        return
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return
    for arg in args.children:
        text = _string_identifier(arg)
        if text:
            names.add(text)


def _string_identifier(node) -> str | None:
    """Return the inner text of a string node iff it is a valid identifier, else None."""
    if node is None or node.type != "string":
        return None
    parts: list[str] = []
    for child in node.children:
        if child.type == "string_content" and child.text:
            parts.append(child.text.decode())
    text = (
        "".join(parts)
        if parts
        else (node.text.decode().strip("\"'") if node.text else "")
    )
    return text if text.isidentifier() else None
