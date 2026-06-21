"""The LLM planner — judgment on top of the deterministic plan, with decision memory.

The deterministic plan handles the mechanical, provably-safe work. This layer asks the
LLM only for judgment the graph can't supply: which god functions to decompose and how
to name the pieces. Crucially it is a *decision loop*: before proposing, it recalls how
a similar shape was refactored before (from agent memory) and reuses that naming; after
proposing, it records the new decision. That recall is what keeps the 2nd, 5th, Nth
similar function consistent — the engine remembers its own conventions instead of
re-deciding per file. If the LLM is unavailable, it returns the deterministic plan
unchanged (the engine never depends on the model being reachable).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from refactorika.analysis.parser import canonical_type_stream, function_text, get_tree
from refactorika.core.schema import PlanItem, RefactorDecision, TransformSpec, Worklist
from refactorika.core.storage import Storage
from refactorika.graph.model import Graph
from refactorika.graph.order import impact_of, topo_order
from refactorika.llm.client import LLMClient
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.decision_memory import DecisionMemory
from refactorika.pipeline.planner import deterministic_plan

_MIN_GOD_LINES = 12  # functions at/over this many lines are decomposition candidates

_SYSTEM = (
    "You are a precise Python refactoring engine. You split a long function into smaller, "
    "well-named helper functions WITHOUT changing behavior. You return ONLY JSON."
)


def make_llm_planner(
    client: Optional[LLMClient] = None,
    memory: Optional[AgentMemory] = None,
    decisions: Optional[DecisionMemory] = None,
):
    """Return a planner(graph, root) closure that adds LLM judgment to the base plan."""

    def _plan(graph: Graph, root: Optional[str] = None) -> Worklist:
        return llm_plan(graph, root=root, client=client, memory=memory, decisions=decisions)

    return _plan


def llm_plan(
    graph: Graph,
    *,
    root: Optional[str],
    client: Optional[LLMClient] = None,
    memory: Optional[AgentMemory] = None,
    decisions: Optional[DecisionMemory] = None,
) -> Worklist:
    base = deterministic_plan(graph, root)
    if root is None:
        return base
    client = client or LLMClient()
    memory = memory or AgentMemory(Storage())
    # Decision memory layers semantic recall over the agent memory (exact shape stays the
    # fast path). Reuse the caller's agent memory so its store/backend is shared.
    dm = decisions or DecisionMemory(memory._storage, agent_memory=memory)
    if not client.available():
        return base

    order, _ = topo_order(graph)
    pos = {q: i for i, q in enumerate(order)}
    extra: list[PlanItem] = []

    for qual, source in _god_functions(graph, root):
        pattern = _shape_pattern(source)
        prior = dm.recall(source, pattern)
        prompt = _decompose_prompt(source, prior)
        resp = client.complete_json(_SYSTEM, prompt)
        if not resp or not resp.get("new_source"):
            continue
        rationale = resp.get("rationale", "decompose god function into named helpers")
        if prior:
            how = (dm.last_match or {}).get("how", "prior")
            rationale += f" (consistent with prior decision, recalled by {how})"
        extra.append(PlanItem(
            spec=TransformSpec(
                kind="decompose_function", target=qual,
                params={"new_source": resp["new_source"]}, rationale=rationale,
            ),
            order_index=pos.get(qual, 0),
            impact=sorted(impact_of(graph, qual)),
        ))
        dm.record(RefactorDecision(
            pattern=pattern, transform_kind="decompose_function", target=qual,
            choice={"helper_names": resp.get("helper_names", [])},
        ), source)

    items = base.items + extra
    items.sort(key=lambda it: it.order_index)
    return Worklist(items=items, cycles=base.cycles)


# --------------------------------------------------------------------------- helpers
def _god_functions(graph: Graph, root: str) -> list[tuple[str, str]]:
    """Top-level functions at/over the size threshold, as (qualname, source)."""
    out: list[tuple[str, str]] = []
    by_file: dict[str, list] = {}
    for q, s in graph.symbols.items():
        if s.kind == "function" and s.scope is None:
            by_file.setdefault(s.file, []).append((q, s))
    for file, syms in by_file.items():
        try:
            source = Path(file).read_text(encoding="utf-8")
            tree = get_tree(source)
        except Exception:
            continue
        # map top-level function name -> node
        nodes = {}
        for child in tree.root_node.children:
            if child.type == "function_definition":
                name_node = child.child_by_field_name("name")
                if name_node is not None and name_node.text:
                    nodes[name_node.text.decode()] = child
        for q, s in syms:
            node = nodes.get(s.name)
            if node is None:
                continue
            size = node.end_point[0] - node.start_point[0] + 1
            if size >= _MIN_GOD_LINES:
                out.append((q, function_text(node, source)))
    return out


def _shape_pattern(source: str) -> str:
    """A structural fingerprint of a function body, so two similarly-shaped functions
    (regardless of names/literals) map to the same recall key for consistent decisions."""
    try:
        tree = get_tree(source)
        node = next(
            c for c in tree.root_node.children if c.type == "function_definition"
        )
        stream = canonical_type_stream(node)
    except Exception:
        stream = [source]
    digest = hashlib.sha256("".join(stream).encode()).hexdigest()[:16]
    return f"decompose:{digest}"


def _decompose_prompt(source: str, prior: Optional[RefactorDecision]) -> str:
    consistency = ""
    if prior and prior.choice.get("helper_names"):
        names = ", ".join(prior.choice["helper_names"])
        consistency = (
            "\n\nA structurally-identical function was already decomposed using these "
            f"helper names: [{names}]. REUSE the same helper names and structure so the "
            "codebase stays consistent."
        )
    return (
        "Split this function into smaller, well-named helper functions. Preserve behavior "
        "exactly (same inputs -> same outputs). Return JSON with keys: "
        '"new_source" (the full replacement source: the helper functions followed by the '
        'slimmed original function, which must keep its original name and signature), '
        '"helper_names" (list of the new helper function names), and "rationale" (one line).'
        f"{consistency}\n\n```python\n{source}\n```"
    )
