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

from refactorika.analysis.parser import (
    canonical_type_stream,
    function_text,
    get_tree,
    max_nesting_depth,
)
from refactorika.core.schema import PlanItem, RefactorDecision, TransformSpec, Worklist
from refactorika.core.storage import Storage
from refactorika.graph.model import Graph
from refactorika.graph.order import impact_of, topo_order
from refactorika.llm.client import LLMClient
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.codebase_index import (
    build_codebase_index,
    codebase_vector_index,
    similar_symbols,
)
from refactorika.memory.decision_memory import DecisionMemory
from refactorika.pipeline.planner import deterministic_plan

# A function is a decomposition candidate if it hits ANY god-function shape — not a single
# line-count proxy. The three axes are independent reasons to split: it's logically complex
# (many branches), it's simply too long, or it's deeply nested. A 12-line straight-line
# function trips none of these; a 10-line 4-deep one trips nesting. Tuned on demo_repo + eval.
_MIN_CYCLOMATIC = 6   # radon cyclomatic complexity (radon grades >10 as concerning)
_MIN_LENGTH = 30      # raw line span — long even if linear
_MIN_NESTING = 4      # control-flow nesting depth

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

    # Semantic codebase index: gives the decompose prompt real neighbor context (the function's
    # semantic peers), instead of judging each function blind. Best-effort — no-op offline.
    cb_vectors = codebase_vector_index(dm.storage, embed_provider=dm.embed)
    build_codebase_index(graph, root, cb_vectors, embed_provider=dm.embed)

    order, _ = topo_order(graph)
    pos = {q: i for i, q in enumerate(order)}
    extra: list[PlanItem] = []

    for qual, source in _god_functions(graph, root):
<<<<<<< HEAD
        pattern = _shape_pattern(source)
        prior = dm.recall(source, pattern)
        neighbors = _neighbor_context(qual, graph, cb_vectors, dm)
        prompt = _decompose_prompt(source, prior, neighbors)
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
=======
        item = decompose_item(
            qual, source, graph,
            client=client, dm=dm, cb_vectors=cb_vectors, order_index=pos.get(qual, 0),
        )
        if item is not None:
            extra.append(item)
>>>>>>> 701786f3f878b5705969b12fbaf395e5ef61172f

    items = base.items + extra
    items.sort(key=lambda it: it.order_index)
    return Worklist(items=items, cycles=base.cycles)


def decompose_item(
    qual: str,
    source: str,
    graph: Graph,
    *,
    client: LLMClient,
    dm: DecisionMemory,
    cb_vectors=None,
    order_index: int = 0,
) -> Optional[PlanItem]:
    """LLM judgment for one god function, as a single reusable decision step.

    Recalls how a structurally-similar function was split before (decision memory), prompts the
    LLM for a behavior-preserving decomposition, returns a ``decompose_function`` PlanItem, and
    records the decision for future consistency. Returns None if the LLM declines or yields
    nothing. This is the single source of truth for the decompose decision — shared by the LLM
    planner and the complexity agent, so the agent spine and the pipeline make identical calls.
    """
    pattern = _shape_pattern(source)
    prior = dm.recall(source, pattern)
    neighbors = _neighbor_context(qual, graph, cb_vectors, dm) if cb_vectors is not None else ""
    prompt = _decompose_prompt(source, prior, neighbors)
    resp = client.complete_json(_SYSTEM, prompt)
    if not resp or not resp.get("new_source"):
        return None
    rationale = resp.get("rationale", "decompose god function into named helpers")
    if prior:
        how = (dm.last_match or {}).get("how", "prior")
        rationale += f" (consistent with prior decision, recalled by {how})"
    item = PlanItem(
        spec=TransformSpec(
            kind="decompose_function", target=qual,
            params={"new_source": resp["new_source"]}, rationale=rationale,
        ),
        order_index=order_index,
        impact=sorted(impact_of(graph, qual)),
    )
    dm.record(RefactorDecision(
        pattern=pattern, transform_kind="decompose_function", target=qual,
        choice={"helper_names": resp.get("helper_names", [])},
    ), source)
    return item


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
            text = function_text(node, source)
            if _is_god_function(node, text):
                out.append((q, text))
    return out


def _is_god_function(node, text: str) -> bool:
    """True if a function hits any god-function shape: complex, long, or deeply nested."""
    length = node.end_point[0] - node.start_point[0] + 1
    if length >= _MIN_LENGTH:
        return True
    if max_nesting_depth(node) >= _MIN_NESTING:
        return True
    return _cyclomatic_complexity(text) >= _MIN_CYCLOMATIC


def _cyclomatic_complexity(source: str) -> int:
    """Max radon cyclomatic complexity among blocks in *source* (0 if unparseable)."""
    try:
        from radon.complexity import cc_visit

        blocks = cc_visit(source)
        return max((b.complexity for b in blocks), default=0)
    except Exception:
        return 0


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


def _neighbor_context(qual, graph, cb_vectors, dm) -> str:
    """A short block naming the function's nearest semantic peers (and how any were
    decomposed before), so the LLM can match existing naming/structure conventions."""
    if not dm.semantic:
        return ""
    hits = similar_symbols(qual, graph, cb_vectors, embed_provider=dm.embed, k=3, threshold=0.5)
    lines: list[str] = []
    for n in hits:
        name = n.meta.get("qualname", n.key)
        prior = dm.agent.get_decision(_shape_pattern_for(graph, n.meta.get("qualname")))
        helpers = prior.choice.get("helper_names") if prior else None
        if helpers:
            lines.append(f"- {name} (similar; previously split into: {', '.join(helpers)})")
        else:
            lines.append(f"- {name} (similar)")
    if not lines:
        return ""
    return "\n\nSemantically similar functions in this codebase:\n" + "\n".join(lines)


def _shape_pattern_for(graph, qualname: Optional[str]) -> str:
    """Recompute the decision key for a neighbor symbol (or a sentinel that never matches)."""
    if not qualname or qualname not in graph.symbols:
        return "decompose:__none__"
    from refactorika.memory.codebase_index import _source_of

    src = _source_of(graph).get(qualname)
    return _shape_pattern(src) if src else "decompose:__none__"


def _decompose_prompt(
    source: str, prior: Optional[RefactorDecision], neighbors: str = ""
) -> str:
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
        f"{consistency}{neighbors}\n\n```python\n{source}\n```"
    )
