"""The Planner — turns the graph into an ordered worklist of transform specs.

Two modes share one output contract (`Worklist`):
- ``deterministic_plan`` needs no LLM: it proposes the safe, mechanical work the graph
  already proves is correct — dead-code removal and per-module cleanup, ordered
  leaf-to-root. This is the always-available spine.
- ``llm_plan`` (in ``planner_llm``) adds judgment calls (rename, decomposition) on top.

A single planner produces the whole worklist so decisions never conflict.
"""

from __future__ import annotations

from refactorika.core.schema import PlanItem, TransformSpec, Worklist
from refactorika.graph.model import Graph
from refactorika.graph.order import impact_of, reachable_from, topo_order


def deterministic_plan(graph: Graph) -> Worklist:
    """Mechanical, no-LLM plan: remove dead code + clean every module, leaf-to-root."""
    order, cycles = topo_order(graph)
    pos = {q: i for i, q in enumerate(order)}
    items: list[PlanItem] = []

    # 1. Dead-code removal — private symbols unreachable from any entry point.
    reach = reachable_from(graph, graph.entry_points)
    dead = [
        q for q in graph.symbols
        if q not in reach
        and graph.symbols[q].kind != "module"
        and graph.symbols[q].is_private  # conservative: only auto-remove private
    ]
    for q in sorted(dead, key=lambda x: pos.get(x, 1 << 30)):
        items.append(PlanItem(
            spec=TransformSpec(
                kind="remove_dead_code", target=q,
                rationale="private symbol unreachable from any entry point",
            ),
            order_index=pos.get(q, 0),
            impact=sorted(impact_of(graph, q)),
        ))

    # 2. Per-module deterministic cleanup.
    for m, sym in graph.symbols.items():
        if sym.kind != "module":
            continue
        module_impact: set[str] = set()
        for q, s in graph.symbols.items():
            if s.file == sym.file and s.kind != "module":
                module_impact |= impact_of(graph, q)
        items.append(PlanItem(
            spec=TransformSpec(
                kind="cleanup", target=m, params={"files": [sym.file]},
                rationale="deterministic cleanup (unused imports, simplifications, format)",
            ),
            order_index=pos.get(m, 0),
            impact=sorted(module_impact),
        ))

    items.sort(key=lambda it: it.order_index)
    return Worklist(items=items, cycles=cycles)
