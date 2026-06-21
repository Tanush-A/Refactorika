"""Base class for all specialist refactor agents."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.apply import apply_and_verify
from ..core.schema import EditRecord, PlanTask
from ..core.storage import Storage


class SpecialistAgent(ABC):
    supported_kinds: list[str] = []

    def handle(self, task: PlanTask, storage: Storage) -> EditRecord:
        new_content = self.propose(task, storage)
        dominant_kind = task.opportunities[0].kind if task.opportunities else self.supported_kinds[0]
        return apply_and_verify(task.file, new_content, dominant_kind, storage)

    @abstractmethod
    def propose(self, task: PlanTask, storage: Storage) -> str: ...
