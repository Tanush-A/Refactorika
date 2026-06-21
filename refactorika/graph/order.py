"""Graph traversals the pipeline runs on: apply order, impact, reachability.

- ``topo_order`` — leaf-to-root: symbols that depend on nothing inside the repo come
  first, so every later refactor builds on already-verified code. Cycles (mutually
  recursive symbols) are condensed into single units via Tarjan SCC and reported, so
  the planner can refactor an SCC together rather than guessing an impossible order.
- ``impact_of`` — root-to-leaf: everything that transitively *depends on* a symbol, so
  after editing it the checker re-verifies only the affected set, not the whole repo.
- ``reachable_from`` — forward reachability from entry points, for dead-code analysis.
"""

from __future__ import annotations

from refactorika.graph.model import Graph


def topo_order(graph: Graph) -> tuple[list[str], list[list[str]]]:
    """Return (leaf-to-root order of all symbols, list of cycle groups).

    Leaf-to-root means: if A references B, B appears before A. Implemented by Tarjan
    SCC condensation (handles cycles) followed by a DFS post-order over the condensed
    DAG, which yields dependencies before dependents.
    """
    sccs = _tarjan_scc(graph)  # already in reverse-topological (leaf-first) order
    node_to_scc: dict[str, int] = {}
    for i, comp in enumerate(sccs):
        for node in comp:
            node_to_scc[node] = i

    order: list[str] = []
    for comp in sccs:
        # deterministic within a component
        order.extend(sorted(comp))

    cycles = [sorted(comp) for comp in sccs if len(comp) > 1]
    return order, cycles


def impact_of(graph: Graph, qualname: str) -> set[str]:
    """All symbols that transitively depend on *qualname* (reverse reachability).

    This is the re-verification scope after editing *qualname*: only these symbols
    (plus the symbol itself) can be affected by the change.
    """
    rev = graph.reverse_edges()
    seen: set[str] = set()
    stack = [qualname]
    while stack:
        cur = stack.pop()
        for dependent in rev.get(cur, set()):
            if dependent not in seen:
                seen.add(dependent)
                stack.append(dependent)
    return seen


def reachable_from(graph: Graph, roots: set[str]) -> set[str]:
    """All symbols reachable by following reference edges forward from *roots*."""
    seen: set[str] = set()
    stack = [r for r in roots if r in graph.symbols]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for dep in graph.outgoing(cur):
            if dep not in seen:
                stack.append(dep)
    return seen


def _tarjan_scc(graph: Graph) -> list[list[str]]:
    """Tarjan's SCC. Returns components in reverse-topological (leaf-first) order.

    A symbol's successors are its dependencies (outgoing reference edges), so the
    natural emission order of Tarjan (a node's SCC is finalized only after all its
    successors') yields dependencies before dependents — exactly leaf-to-root.
    """
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []

    nodes = sorted(graph.symbols.keys())

    def strongconnect(v: str) -> None:
        # Iterative DFS to avoid recursion limits on large graphs.
        work = [(v, iter(sorted(graph.outgoing(v))))]
        indices[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        while work:
            node, it = work[-1]
            advanced = False
            for w in it:
                if w not in indices:
                    indices[w] = lowlink[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(graph.outgoing(w)))))
                    advanced = True
                    break
                elif w in on_stack:
                    lowlink[node] = min(lowlink[node], indices[w])
            if advanced:
                continue
            work.pop()
            if lowlink[node] == indices[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                result.append(comp)
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

    for node in nodes:
        if node not in indices:
            strongconnect(node)
    return result
