"""Dead-code detection via call-graph reachability.

BFS/DFS from entry points; anything not reachable is a dead-code candidate.
Confidence is assigned based on naming conventions and string-literal reflection risk.
"""

from __future__ import annotations

import re
from pathlib import Path

from refactorika.analysis.call_graph import CallGraph, _collect_py_files
from refactorika.core.schema import DeadSymbol
from refactorika.core.storage import Storage


def find_dead_code(path: str, storage: Storage) -> dict:
    """Detect unreachable symbols in *path* via call-graph reachability.

    Parameters
    ----------
    path:
        File or directory to analyse.
    storage:
        Storage instance (unused for caching in this version but kept in the
        signature per the module contract so callers can pass it freely).

    Returns
    -------
    dict with keys:
        "path"          - the analysed path
        "entry_points"  - list of qualnames used as BFS roots
        "dead_symbols"  - list of DeadSymbol.to_dict() sorted by rank descending
    """
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

    # Collect all string-literal names across the project (for reflection check)
    string_names = _collect_string_names(path)

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

        # Assign confidence + reason
        if unqualified.startswith("_") and sites == 0:
            confidence = "high"
            rank = 90
            reason = f"Private name '{unqualified}' with zero call sites and unreachable from entry points."
        elif sites == 0:
            # Check if it might be referenced via string / reflection
            if unqualified in string_names:
                confidence = "low"
                rank = 30
                reason = (
                    f"Public name '{unqualified}' has zero call sites but appears in a string "
                    "literal — possible getattr/reflection usage."
                )
            else:
                confidence = "medium"
                rank = 60
                reason = (
                    f"Public name '{unqualified}' has zero call sites within the analysed codebase "
                    "and is unreachable from entry points."
                )
        else:
            # Has call sites but still unreachable — unusual; treat as low confidence
            if unqualified in string_names:
                confidence = "low"
                rank = 30
            else:
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

    return {
        "path": path,
        "entry_points": sorted(entry_pts),
        "dead_symbols": [d.to_dict() for d in dead],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_string_names(path: str) -> set[str]:
    """Return all bare identifiers that appear inside string literals across *path*."""
    names: set[str] = set()
    files, _ = _collect_py_files(path)
    for fpath in files:
        try:
            source = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']', source):
            names.add(m.group(1))
    return names
