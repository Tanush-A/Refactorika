"""DuplicateAgent: consolidates duplicate functions.

propose() is a stub — wire in LLM reasoning to generate merged new_content.
Multi-file consolidation overrides handle() to use apply_and_verify_multi.
"""

from __future__ import annotations

from pathlib import Path

from ..core.schema import EditRecord, PlanTask
from ..core.storage import Storage
from .base import SpecialistAgent


class DuplicateAgent(SpecialistAgent):
    supported_kinds = ["consolidate_duplicate"]

    def handle(self, task: PlanTask, storage: Storage) -> EditRecord:
        # Stub delegates to single-file base path.
        # Override here to call apply_and_verify_multi once LLM wiring provides
        # new content for both files in a duplicate pair.
        return super().handle(task, storage)

    def propose(self, task: PlanTask, storage: Storage) -> str:
        # Stub: returns original content (no-op) until LLM reasoning is wired in.
        return Path(task.file).read_text()
