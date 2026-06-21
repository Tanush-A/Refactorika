"""Base class for all specialist refactor agents.

An agent supplies *judgment* (which symbol, what new name, whether/how to split). The
deterministic transform engines supply *correctness* (reference-correct mutation) and the
Checker supplies *proof* (impact-scoped gate stack, commit or revert). When the orchestrator
hands an agent a graph + a shared Checker, the agent routes its decision through that
verified spine (``propose_specs`` -> ``dispatch`` -> ``checker.verify_apply``). Agents/kinds
without a deterministic engine fall back to the legacy text path (``propose`` -> full-file
``apply_and_verify``).
"""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..core.apply import apply_and_verify
from ..core.schema import EditRecord, PlanTask, TransformSpec
from ..core.storage import Storage

if TYPE_CHECKING:
    from ..graph.model import Graph
    from ..pipeline.checker import Checker


class SpecialistAgent(ABC):
    supported_kinds: list[str] = []

    def handle(
        self,
        task: PlanTask,
        storage: Storage,
        *,
        graph: "Optional[Graph]" = None,
        checker: "Optional[Checker]" = None,
    ) -> EditRecord:
        """Produce and verify a change for *task*.

        Preferred path (graph + checker supplied): the agent emits deterministic TransformSpecs
        which the engines apply and the checker verifies with impact-scoped tests. Falls back to
        the legacy text path when no engine path is available for this agent/task.
        """
        if graph is not None and checker is not None:
            specs = self.propose_specs(task, storage, graph, str(checker.root))
            if specs:
                return self._apply_verified(specs, checker)
        new_content = self.propose(task, storage)
        dominant_kind = (
            task.opportunities[0].kind if task.opportunities else self.supported_kinds[0]
        )
        return apply_and_verify(task.file, new_content, dominant_kind, storage)

    def propose_specs(
        self, task: PlanTask, storage: Storage, graph: "Graph", root: str
    ) -> list[TransformSpec]:
        """Deterministic-engine judgments for this task. Default: none (use the text path)."""
        return []

    def propose(self, task: PlanTask, storage: Storage) -> str:
        """Legacy full-file proposal. Override for text-path agents; default is a no-op."""
        return Path(task.file).read_text(encoding="utf-8")

    # ------------------------------------------------------------------ internal
    def _apply_verified(self, specs: list[TransformSpec], checker: "Checker") -> EditRecord:
        """Apply each spec through its engine + the checker, rebuilding the graph between specs.

        A committed spec shifts symbol positions/qualnames, so the graph is rebuilt before each
        one (mirrors the pipeline orchestrator). Returns the last edit record.
        """
        from ..graph.order import impact_of
        from ..graph.resolver import build_graph
        from ..pipeline.checker import impacted_test_node_ids
        from ..transforms.base import dispatch

        root = str(checker.root)
        last: Optional[EditRecord] = None
        for spec in specs:
            graph = build_graph(root)
            if spec.kind != "cleanup" and spec.target not in graph.symbols:
                continue  # already removed/renamed by a prior spec
            edits = dispatch(spec, root, graph)
            if not edits:
                continue
            node_ids = impacted_test_node_ids(graph, root, sorted(impact_of(graph, spec.target)))
            last = checker.verify_apply(edits, spec.kind, test_node_ids=node_ids)
        return last or EditRecord(
            file="", refactor_kind="", status="skipped-needs-human",
            failure_reason="agent proposed no applicable specs",
        )
