"""Vertical slice: ComplexityAgent = LLM judgment + deterministic, verified correctness.

The agent decides *how* to split a god function (LLM, via the shared decompose decision); the
deterministic ``decompose_function`` engine applies it as an AST-node replacement; the Checker
proves behavior is preserved (impact-scoped tests) and commits. Proven fully offline with a stub
LLM — no API key, no network — exactly like the pipeline planner tests.
"""

from __future__ import annotations

import subprocess

from refactorika.agents.complexity_agent import ComplexityAgent
from refactorika.core.schema import Opportunity, PlanTask
from refactorika.core.storage import Storage
from refactorika.graph.resolver import build_graph
from refactorika.llm.client import LLMClient
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.decision_memory import DecisionMemory
from refactorika.pipeline.checker import Checker
from refactorika.pipeline.planner_llm import (
    _SYSTEM,
    _decompose_prompt,
    _god_functions,
    _shape_pattern,
)

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


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def _stub_client(graph, root: str) -> LLMClient:
    """Stub keyed on the exact decompose prompt the agent will send (no prior, no neighbors)."""
    resp = {"new_source": _DECOMPOSED, "helper_names": ["_accumulate"], "rationale": "split"}
    keyer = LLMClient()  # default provider — keys match the agent's default client
    stub = {}
    for _qual, source in _god_functions(graph, root):
        stub[keyer.cache_key(_SYSTEM, _decompose_prompt(source, None))] = resp
    return LLMClient(stub=stub)


def test_complexity_agent_decomposes_via_engine_and_verifies(tmp_path):
    (tmp_path / "m.py").write_text(_GOD)
    (tmp_path / "test_m.py").write_text(
        "from m import score\n\n"
        "def test_score():\n"
        "    assert score([1, 2], 'a', True) == (2 + 4) + 10\n"
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")

    root = str(tmp_path)
    graph = build_graph(root)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    dm = DecisionMemory(storage, agent_memory=AgentMemory(storage))
    agent = ComplexityAgent(client=_stub_client(graph, root), decisions=dm)
    checker = Checker(root, storage=storage, run_tests=True)

    task = PlanTask(
        file=str(tmp_path / "m.py"),
        opportunities=[
            Opportunity(kind="decompose_function", location="score", detail="god fn", rank=10)
        ],
        dependents=[],
        order=0,
    )

    rec = agent.handle(task, storage, graph=graph, checker=checker)

    # Correctness: the deterministic engine applied it and the verified spine committed it green.
    assert rec.status == "committed", rec.failure_reason
    assert rec.refactor_kind == "decompose_function"
    assert rec.checks.tests is True
    assert "_accumulate" in (tmp_path / "m.py").read_text()
    # Behavior preserved end to end: the full suite is still green.
    ok, _ = checker.run_full_suite()
    assert ok is True
    # Judgment recorded for future consistency (the decision-memory beat).
    decision = dm.agent.get_decision(_shape_pattern(_GOD))
    assert decision is not None and decision.choice["helper_names"] == ["_accumulate"]


def test_dispatch_plan_runs_complexity_agent_through_engine(tmp_path):
    """Orchestrator wiring: a confirmed plan routes a complexity task to the ComplexityAgent,
    which decomposes via the deterministic engine + checker. Proven offline with a stub LLM."""
    from refactorika.agents.complexity_agent import ComplexityAgent as _CA
    from refactorika.agents.orchestrator import dispatch_plan
    from refactorika.core.schema import Plan, PlanTask

    (tmp_path / "m.py").write_text(_GOD)
    (tmp_path / "test_m.py").write_text(
        "from m import score\n\n"
        "def test_score():\n"
        "    assert score([1, 2], 'a', True) == (2 + 4) + 10\n"
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")

    root = str(tmp_path)
    graph = build_graph(root)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    dm = DecisionMemory(storage, agent_memory=AgentMemory(storage))
    agent = _CA(client=_stub_client(graph, root), decisions=dm)

    plan = Plan(
        repo=root,
        dominant_finding="god function",
        tasks=[PlanTask(
            file=str(tmp_path / "m.py"),
            opportunities=[Opportunity(kind="split_function", location="score",
                                       detail="god fn", rank=10)],
            dependents=[],
            order=0,
        )],
        confirmed=True,
    )
    storage.save_plan(plan.to_dict())

    summary = dispatch_plan(storage, specialists=[agent], run_tests=True)

    assert summary["committed"] == 1, summary
    assert "_accumulate" in (tmp_path / "m.py").read_text()


class _DownClient:
    """A generation client that is simply not reachable."""

    def available(self) -> bool:
        return False


def test_complexity_agent_without_llm_falls_back_to_no_specs(tmp_path):
    """No reachable LLM -> the agent proposes nothing (engine never depends on the model)."""
    (tmp_path / "m.py").write_text(_GOD)
    root = str(tmp_path)
    graph = build_graph(root)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")

    agent = ComplexityAgent(client=_DownClient())
    specs = agent.propose_specs(
        PlanTask(file=str(tmp_path / "m.py"), opportunities=[], dependents=[], order=0),
        storage, graph, root,
    )
    assert specs == []
