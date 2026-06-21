"""The whole-program model: real reference resolution, the symbol graph, and ordering.

This package is the correctness foundation. `resolver.py` builds a reference-correct
symbol graph via Jedi static analysis (real name binding across imports/scopes, not
regex name-matching). `model.py` is the graph data structure. `order.py` provides the
leaf-to-root apply order and root-to-leaf impact analysis the pipeline runs on.
"""

from refactorika.graph.model import Graph, Symbol
from refactorika.graph.order import impact_of, reachable_from, topo_order

__all__ = ["Graph", "Symbol", "topo_order", "impact_of", "reachable_from"]
