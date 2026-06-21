"""Resolver correctness — the spine the whole product's value rests on.

These tests assert *reference-correctness*: edges connect a use to its true binding,
not to any same-named symbol. The discriminating case (`test_same_name_no_false_edge`)
is precisely the failure mode of the old regex call-graph this resolver replaces.
"""

from __future__ import annotations

from pathlib import Path

from refactorika.graph.order import impact_of, reachable_from, topo_order
from refactorika.graph.resolver import build_graph


def _write(tmp_path: Path, files: dict[str, str]) -> str:
    for name, src in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return str(tmp_path)


def test_cross_file_edge_resolves_to_true_definition(tmp_path):
    root = _write(tmp_path, {
        "util.py": "def helper(x):\n    return x + 1\n",
        "app.py": "from util import helper\n\ndef run():\n    return helper(41)\n",
    })
    g = build_graph(root)
    assert "util.helper" in g.symbols
    assert "app.run" in g.symbols
    assert "util.helper" in g.outgoing("app.run")


def test_same_name_no_false_edge(tmp_path):
    """A call to a.process must NOT create an edge to b.process (the old heuristic's bug)."""
    root = _write(tmp_path, {
        "a.py": "def process():\n    return 'a'\n",
        "b.py": "def process():\n    return 'b'\n",
        "caller.py": "from a import process\n\ndef go():\n    return process()\n",
    })
    g = build_graph(root)
    outs = g.outgoing("caller.go")
    assert "a.process" in outs
    assert "b.process" not in outs


def test_aliased_import_resolves(tmp_path):
    root = _write(tmp_path, {
        "m.py": "def original():\n    return 1\n",
        "c.py": "from m import original as aliased\n\ndef use():\n    return aliased()\n",
    })
    g = build_graph(root)
    assert "m.original" in g.outgoing("c.use")


def test_private_unreferenced_is_dead_but_reached_is_not(tmp_path):
    root = _write(tmp_path, {
        "lib.py": (
            "def _dead():\n    return 0\n\n"
            "def _used():\n    return 1\n\n"
            "def public():\n    return _used()\n"
        ),
    })
    g = build_graph(root)
    reach = reachable_from(g, g.entry_points)
    dead = {q for q in g.symbols if q not in reach and g.symbols[q].kind != "module"}
    assert "lib._dead" in dead          # private, nothing references it
    assert "lib._used" not in dead      # reached via the public entry point
    assert "lib.public" not in dead     # public symbol is itself an entry point


def test_method_dispatch_edge(tmp_path):
    root = _write(tmp_path, {
        "svc.py": (
            "class Service:\n"
            "    def helper(self):\n        return 1\n\n"
            "    def run(self):\n        return self.helper()\n"
        ),
    })
    g = build_graph(root)
    assert g.symbols["svc.Service.helper"].kind == "method"
    assert "svc.Service.helper" in g.outgoing("svc.Service.run")


def test_impact_is_reverse_reachability(tmp_path):
    root = _write(tmp_path, {
        "base.py": "def leaf():\n    return 1\n",
        "mid.py": "from base import leaf\n\ndef mid():\n    return leaf()\n",
        "top.py": "from mid import mid\n\ndef top():\n    return mid()\n",
    })
    g = build_graph(root)
    impact = impact_of(g, "base.leaf")
    assert impact == {"mid.mid", "top.top"}


def test_leaf_to_root_orders_dependencies_first(tmp_path):
    root = _write(tmp_path, {
        "base.py": "def leaf():\n    return 1\n",
        "mid.py": "from base import leaf\n\ndef mid():\n    return leaf()\n",
    })
    g = build_graph(root)
    order, cycles = topo_order(g)
    assert cycles == []
    assert order.index("base.leaf") < order.index("mid.mid")


def test_cycle_is_reported_and_all_nodes_ordered(tmp_path):
    root = _write(tmp_path, {
        "cyc.py": (
            "def ping(n):\n    return pong(n)\n\n"
            "def pong(n):\n    return ping(n)\n"
        ),
    })
    g = build_graph(root)
    order, cycles = topo_order(g)
    # both symbols still appear exactly once
    assert {"cyc.ping", "cyc.pong"} <= set(order)
    # the mutual recursion is reported as one cycle group
    assert any(set(c) == {"cyc.ping", "cyc.pong"} for c in cycles)


def test_graph_roundtrips_through_dict(tmp_path):
    root = _write(tmp_path, {
        "m.py": "def a():\n    return b()\n\ndef b():\n    return 1\n",
    })
    g = build_graph(root)
    from refactorika.graph.model import Graph
    g2 = Graph.from_dict(g.to_dict())
    assert g2.symbols.keys() == g.symbols.keys()
    assert g2.outgoing("m.a") == g.outgoing("m.a")
    assert g2.entry_points == g.entry_points
