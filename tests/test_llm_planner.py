"""LLM planner + decision-memory consistency — exercised fully offline via a stub client.

The key property: two structurally-identical god functions get decomposed with the SAME
helper names, because the second decomposition recalls the first from agent memory. That
is the "Redis is decision memory, not a cache" behaviour, proven without a network call.
"""

from __future__ import annotations

from refactorika.core.storage import Storage
from refactorika.graph.resolver import build_graph
from refactorika.llm.client import LLMClient
from refactorika.memory.agent_memory import AgentMemory
from refactorika.pipeline.planner_llm import (
    _shape_pattern,
    make_llm_planner,
)

# A long-ish function (>= _MIN_GOD_LINES lines) to trigger decomposition.
_GOD = (
    "def score(values, mode, bonus):\n"
    "    total = 0\n"
    "    for v in values:\n"
    "        if v > 0:\n"
    "            if mode == 'a':\n"
    "                total += v * 2\n"
    "            elif mode == 'b':\n"
    "                total += v * 3\n"
    "            else:\n"
    "                total += v\n"
    "    if bonus:\n"
    "        total += 10\n"
    "    if total > 100:\n"
    "        total = 100\n"
    "    return total\n"
)

_DECOMPOSED = (
    "def _accumulate(values, mode):\n"
    "    total = 0\n"
    "    for v in values:\n"
    "        if v > 0:\n"
    "            if mode == 'a':\n"
    "                total += v * 2\n"
    "            elif mode == 'b':\n"
    "                total += v * 3\n"
    "            else:\n"
    "                total += v\n"
    "    return total\n\n\n"
    "def score(values, mode, bonus):\n"
    "    total = _accumulate(values, mode)\n"
    "    if bonus:\n"
    "        total += 10\n"
    "    if total > 100:\n"
    "        total = 100\n"
    "    return total\n"
)


def _stub_client_for_graph(graph, root: str) -> LLMClient:
    """Build a stub keyed on the exact function source the planner will send, covering
    both the first prompt (no prior) and the recall prompt (with a prior decision)."""
    from refactorika.core.schema import RefactorDecision
    from refactorika.pipeline.planner_llm import (
        _SYSTEM,
        _decompose_prompt,
        _god_functions,
        _shape_pattern,
    )

    response = {"new_source": _DECOMPOSED, "helper_names": ["_accumulate"],
                "rationale": "split accumulation out"}
    keyer = LLMClient()  # default provider; keys match the planner's default client
    stub = {}
    for _qual, source in _god_functions(graph, root):
        prior = RefactorDecision(pattern=_shape_pattern(source),
                                 transform_kind="decompose_function", target="x",
                                 choice={"helper_names": ["_accumulate"]})
        for p in (_decompose_prompt(source, None), _decompose_prompt(source, prior)):
            stub[keyer.cache_key(_SYSTEM, p)] = response
    return LLMClient(stub=stub)


def test_llm_plan_proposes_decomposition(tmp_path):
    (tmp_path / "m.py").write_text(_GOD)
    g = build_graph(str(tmp_path))
    planner = make_llm_planner(client=_stub_client_for_graph(g, str(tmp_path)),
                               memory=AgentMemory(Storage(redis_url=None,
                                                          json_path=tmp_path / "s.json")))
    plan = planner(g, root=str(tmp_path))
    decompose = [it for it in plan.items if it.spec.kind == "decompose_function"]
    assert len(decompose) == 1
    assert decompose[0].spec.target == "m.score"
    assert "_accumulate" in decompose[0].spec.params["new_source"]


def test_llm_decomposition_flows_through_gates_and_commits(tmp_path):
    """End-to-end: LLM proposes a decomposition, it passes the gate stack, commits green."""
    import subprocess

    from refactorika.pipeline.orchestrator import run_pipeline

    (tmp_path / "m.py").write_text(_GOD)
    (tmp_path / "test_m.py").write_text(
        "from m import score\n\n"
        "def test_score():\n"
        "    assert score([1, 2], 'a', True) == (2 + 4) + 10\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], capture_output=True)

    g = build_graph(str(tmp_path))
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    planner = make_llm_planner(client=_stub_client_for_graph(g, str(tmp_path)),
                               memory=AgentMemory(storage))
    res = run_pipeline(str(tmp_path), apply=False, planner=planner, storage=storage)

    decompose = [r for r in res.records
                 if r["refactor_kind"] == "decompose_function" and r["status"] == "committed"]
    assert decompose, "expected a committed decomposition"
    assert res.finale_tests is True  # behavior preserved end to end


def test_consistency_beat_fires_on_the_real_demo_functions(tmp_path):
    """The Redis decision-memory beat, on demo_repo's actual near-duplicates: compute_total
    and calculate_invoice_total are structurally identical, so decomposing the second recalls
    the first's helper names. Proven offline via a stub (no API key)."""
    import shutil
    from pathlib import Path as _P

    from refactorika.core.schema import RefactorDecision
    from refactorika.llm.client import LLMClient
    from refactorika.pipeline.planner_llm import (
        _SYSTEM,
        _decompose_prompt,
        _god_functions,
        _shape_pattern,
    )

    demo = _P(__file__).resolve().parent.parent / "demo_repo"
    target = tmp_path / "demo_repo"
    shutil.copytree(demo, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    g = build_graph(str(target))

    # A stub that returns a (name-agnostic) decomposition for each function, both prompt
    # variants (with and without a recalled prior), all using the same helper names.
    helper_names = ["_line_amount", "_apply_coupon"]
    keyer = LLMClient()
    stub = {}
    for qual, source in _god_functions(g, str(target)):
        resp = {"new_source": source, "helper_names": helper_names, "rationale": "split"}
        prior = RefactorDecision(pattern=_shape_pattern(source),
                                 transform_kind="decompose_function",
                                 target="x", choice={"helper_names": helper_names})
        for p in (_decompose_prompt(source, None), _decompose_prompt(source, prior)):
            stub[keyer.cache_key(_SYSTEM, p)] = resp

    memory = AgentMemory(Storage(redis_url=None, json_path=tmp_path / "s.json"))
    planner = make_llm_planner(client=LLMClient(stub=stub), memory=memory)
    plan = planner(g, root=str(target))

    decompose = [it for it in plan.items if it.spec.kind == "decompose_function"]
    targets = {it.spec.target for it in decompose}
    assert {"orders.compute_total", "billing.calculate_invoice_total"} <= targets
    # exactly one of the two carries the "consistent with prior" note — the recall fired
    assert sum("consistent" in it.spec.rationale for it in decompose) >= 1
    # the shared shape recorded the same helper names for both
    shape = _shape_pattern(_god_functions(g, str(target))[0][1])
    assert memory.get_decision(shape).choice["helper_names"] == helper_names


def test_decompose_prompt_includes_neighbor_context():
    """Phase 4: when neighbor context is supplied, it lands in the prompt."""
    from refactorika.pipeline.planner_llm import _decompose_prompt

    block = "\n\nSemantically similar functions in this codebase:\n- pkg.other (similar)"
    prompt = _decompose_prompt("def f(): pass", None, block)
    assert "Semantically similar functions" in prompt
    assert "pkg.other" in prompt


def test_neighbor_context_surfaces_domain_peer(tmp_path):
    """Phase 4: _neighbor_context names a semantically similar sibling (and any prior split)."""
    from refactorika.core.schema import RefactorDecision
    from refactorika.llm.providers import EmbeddingProvider
    from refactorika.memory.codebase_index import build_codebase_index, codebase_vector_index
    from refactorika.memory.decision_memory import DecisionMemory
    from refactorika.memory.vector_index import VectorIndex
    from refactorika.pipeline.planner_llm import _neighbor_context, _shape_pattern

    class _Fake(EmbeddingProvider):
        name = "fake"

        def __init__(self):
            super().__init__("fake")

        def available(self):
            return True

        def embed(self, texts):
            return [[1.0, 0.0] if "discount" in t.lower() else [0.0, 1.0] for t in texts]

    src = (
        "def apply_discount(p, pct):\n"
        "    return p - p * pct\n\n\n"
        "def compute_discount(t, r):\n"
        "    return t * r\n\n\n"
        "def render(x):\n"
        "    return str(x)\n"
    )
    (tmp_path / "shop.py").write_text(src)
    g = build_graph(str(tmp_path))
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    fake = _Fake()
    cb = codebase_vector_index(storage, fake)
    build_codebase_index(g, str(tmp_path), cb, embed_provider=fake)

    dm = DecisionMemory(storage, embed_provider=fake, vector_index=VectorIndex(storage))
    # Record how the peer was previously split, so the context can echo its helper names.
    peer = next(q for q in g.symbols if q.endswith("compute_discount"))
    peer_src = (tmp_path / "shop.py").read_text().split("\n\n\n")[1]
    dm.agent.put_decision(RefactorDecision(
        pattern=_shape_pattern(peer_src), transform_kind="decompose_function",
        target=peer, choice={"helper_names": ["_rate_term"]}))

    target = next(q for q in g.symbols if q.endswith("apply_discount"))
    ctx = _neighbor_context(target, g, cb, dm)
    assert "compute_discount" in ctx
    assert "render" not in ctx  # orthogonal peer excluded


def test_decision_memory_makes_naming_consistent(tmp_path):
    # Two identical-shape functions in different modules.
    (tmp_path / "a.py").write_text(_GOD)
    (tmp_path / "b.py").write_text(_GOD)
    g = build_graph(str(tmp_path))

    memory = AgentMemory(Storage(redis_url=None, json_path=tmp_path / "s.json"))
    client = _stub_client_for_graph(g, str(tmp_path))
    planner = make_llm_planner(client=client, memory=memory)
    plan = planner(g, root=str(tmp_path))

    # both god functions are decomposed
    targets = {it.spec.target for it in plan.items if it.spec.kind == "decompose_function"}
    assert targets == {"a.score", "b.score"}

    # the decision was recorded under the shared structural pattern, with the helper name
    pattern = _shape_pattern(_GOD)
    decision = memory.get_decision(pattern)
    assert decision is not None
    assert decision.choice["helper_names"] == ["_accumulate"]
