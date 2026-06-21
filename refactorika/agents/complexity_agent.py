"""ComplexityAgent: god-function decomposition — LLM judgment, deterministic correctness.

The agent brings the judgment (which functions are god functions, how to split them, what to
name the helpers — consistently, via decision memory). The deterministic ``decompose_function``
engine (AST-node replacement) and the verified Checker bring the correctness. The LLM decision
is the *same* one the pipeline planner makes — both call ``planner_llm.decompose_item`` — so the
agent spine and the autonomous pipeline stay in lock-step.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..core.schema import PlanTask, TransformSpec
from ..core.storage import Storage
from .base import SpecialistAgent

if TYPE_CHECKING:
    from ..graph.model import Graph
    from ..llm.client import LLMClient
    from ..memory.decision_memory import DecisionMemory


class ComplexityAgent(SpecialistAgent):
    supported_kinds = [
        "split_function",
        "flatten_nesting",
        "extract_helper",
        "split_module",
        "dedupe_block",
        "decompose_function",
    ]

    def __init__(
        self,
        client: "Optional[LLMClient]" = None,
        decisions: "Optional[DecisionMemory]" = None,
    ) -> None:
        # Injectable for tests/offline; constructed lazily from storage otherwise.
        self._client = client
        self._decisions = decisions

    def propose_specs(
        self, task: PlanTask, storage: Storage, graph: "Graph", root: str
    ) -> list[TransformSpec]:
        """Decompose every god function in task.file, as deterministic decompose specs.

        Returns [] when no LLM is reachable — the engine never depends on the model being up;
        the orchestrator simply gets no complexity work for this task.
        """
        from ..llm.client import LLMClient
        from ..memory.agent_memory import AgentMemory
        from ..memory.decision_memory import DecisionMemory
        from ..pipeline.planner_llm import _god_functions, decompose_item

        client = self._client or LLMClient()
        if not client.available():
            return []
        dm = self._decisions or DecisionMemory(storage, agent_memory=AgentMemory(storage))

        target_file = Path(task.file).resolve()
        specs: list[TransformSpec] = []
        for qual, source in _god_functions(graph, root):
            sym = graph.symbols.get(qual)
            if sym is None or Path(sym.file).resolve() != target_file:
                continue
            item = decompose_item(qual, source, graph, client=client, dm=dm)
            if item is not None:
                specs.append(item.spec)
        return specs
