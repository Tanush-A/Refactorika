"""DeadCodeAgent: removes high-confidence dead symbols (deterministic, no LLM needed)."""

from __future__ import annotations

from ..core.schema import PlanTask
from ..core.storage import Storage
from .base import SpecialistAgent


class DeadCodeAgent(SpecialistAgent):
    supported_kinds = ["remove_dead_code"]

    def propose(self, task: PlanTask, storage: Storage) -> str:
        from ..analysis.dead_code import find_dead_code
        from ..transforms.dead import remove_dead_symbols

        result = find_dead_code(task.file, storage)
        high_conf_names = {
            s["name"].split(".")[-1]
            for s in result.get("dead_symbols", [])
            if s["confidence"] == "high"
        }
        return remove_dead_symbols(task.file, high_conf_names)
