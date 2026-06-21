"""Frozen contract shared by every shell. Change with care — this is the interface."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

Status = Literal["committed", "rolled-back", "skipped-needs-human"]

# Refactor kinds Claude may propose (organization + complexity).
REFACTOR_KINDS = (
    "split_module",
    "reorder_imports",
    "extract_helper",
    "split_function",
    "flatten_nesting",
    "dedupe_block",
)


@dataclass
class Opportunity:
    """A single ranked refactor opportunity found by analysis."""

    kind: str
    location: str  # function name or "line A-B"
    detail: str
    rank: int  # higher = more worth doing

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalysisResult:
    file: str
    opportunities: list[Opportunity] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"file": self.file, "opportunities": [o.to_dict() for o in self.opportunities]}


@dataclass
class GateChecks:
    """Each gate is True (passed), False (failed), or None (skipped — recorded, never silent)."""

    parse: Optional[bool] = None
    lint: Optional[bool] = None
    typecheck: Optional[bool] = None
    tests: Optional[bool] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EditRecord:
    file: str
    refactor_kind: str
    checks: GateChecks = field(default_factory=GateChecks)
    retries: int = 0
    status: Status = "rolled-back"
    failure_reason: Optional[str] = None
    diff: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "refactor_kind": self.refactor_kind,
            "checks": self.checks.to_dict(),
            "retries": self.retries,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "diff": self.diff,
        }
