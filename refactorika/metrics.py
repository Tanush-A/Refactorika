"""Before/after repo metrics for the report: lines, complexity, dead code.

Deterministic and dependency-light (radon for LOC + cyclomatic complexity, the graph
for dead-code count) so the CLI can show a concrete "what got better" table.
"""

from __future__ import annotations

from refactorika.graph.order import reachable_from
from refactorika.graph.resolver import build_graph, collect_py_files


def repo_metrics(root: str) -> dict:
    """Return a metrics snapshot of the repo at *root*."""
    from radon.complexity import cc_visit
    from radon.raw import analyze

    files, _ = collect_py_files(root)
    sloc = 0
    lloc = 0
    complexities: list[int] = []
    for f in files:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
            raw = analyze(src)
            sloc += raw.sloc
            lloc += raw.lloc
            complexities.extend(block.complexity for block in cc_visit(src))
        except Exception:
            continue

    graph = build_graph(root)
    reach = reachable_from(graph, graph.entry_points)
    dead = [
        q for q in graph.symbols
        if q not in reach and graph.symbols[q].kind != "module"
    ]

    return {
        "files": len(files),
        "sloc": sloc,
        "lloc": lloc,
        "functions": len(complexities),
        "avg_complexity": round(sum(complexities) / len(complexities), 2) if complexities else 0,
        "max_complexity": max(complexities) if complexities else 0,
        "dead_symbols": len(dead),
    }


def metrics_delta(before: dict, after: dict) -> dict:
    """Signed change per metric (after - before)."""
    return {k: after.get(k, 0) - before.get(k, 0) for k in before}
