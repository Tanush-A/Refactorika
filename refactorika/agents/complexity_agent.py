"""ComplexityAgent: handles function splits, nesting flattening, helper extraction.

propose() is a stub — wire in LLM reasoning to generate new_content.
"""

from __future__ import annotations

from pathlib import Path

from ..core.schema import PlanTask
from ..core.storage import Storage
from .base import SpecialistAgent


class ComplexityAgent(SpecialistAgent):
    supported_kinds = [
        "split_function",
        "flatten_nesting",
        "extract_helper",
        "split_module",
        "dedupe_block",
    ]

    def propose(self, task: PlanTask, storage: Storage) -> str:
        # Stub: returns original content (no-op) until LLM reasoning is wired in.
        # Replace with an LLM call that receives task.opportunities + file content
        # and returns refactored source.
        return Path(task.file).read_text()
