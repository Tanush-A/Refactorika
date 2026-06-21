"""ImportAgent: reorders and deduplicates imports (deterministic, no LLM needed)."""

from __future__ import annotations

from ..core.schema import PlanTask
from ..core.storage import Storage
from .base import SpecialistAgent


class ImportAgent(SpecialistAgent):
    supported_kinds = ["reorder_imports"]

    def propose(self, task: PlanTask, storage: Storage) -> str:
        from ..transforms.imports import reorder_imports
        return reorder_imports(task.file)
