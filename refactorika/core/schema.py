"""Frozen contract shared by every shell. Change with care — this is the interface."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

Status = Literal["committed", "rolled-back", "skipped-needs-human"]

REFACTOR_KINDS = (
    "split_module",
    "reorder_imports",
    "extract_helper",
    "split_function",
    "flatten_nesting",
    "dedupe_block",
    "consolidate_duplicate",
    "remove_dead_code",
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
    files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.files:
            self.files = [self.file]

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "files": self.files,
            "refactor_kind": self.refactor_kind,
            "checks": self.checks.to_dict(),
            "retries": self.retries,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "diff": self.diff,
        }


# ---------------------------------------------------------------------------
# V2 result types
# ---------------------------------------------------------------------------

@dataclass
class SymbolRef:
    file: str
    name: str
    line: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DuplicatePair:
    a: SymbolRef
    b: SymbolRef
    similarity: float
    match_type: str  # "structural" | "semantic"
    consolidation_target: SymbolRef
    reason: str
    rank: int

    def to_dict(self) -> dict:
        return {
            "a": self.a.to_dict(),
            "b": self.b.to_dict(),
            "similarity": self.similarity,
            "match_type": self.match_type,
            "consolidation_target": self.consolidation_target.to_dict(),
            "reason": self.reason,
            "rank": self.rank,
        }


@dataclass
class DeadSymbol:
    kind: str  # "function" | "class" | "assignment"
    name: str
    file: str
    line: int
    confidence: str  # "high" | "medium" | "low"
    reason: str
    rank: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExportRef:
    name: str
    kind: str  # "function" | "class" | "assignment"
    signature: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModuleContext:
    path: str
    purpose_hint: str
    exports: list[ExportRef] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    changed_since_last: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "purpose_hint": self.purpose_hint,
            "exports": [e.to_dict() for e in self.exports],
            "dependents": self.dependents,
            "flagged": self.flagged,
            "changed_since_last": self.changed_since_last,
            "decisions": self.decisions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleContext":
        exports = [ExportRef(**e) for e in d.get("exports", [])]
        return cls(
            path=d["path"],
            purpose_hint=d.get("purpose_hint", ""),
            exports=exports,
            dependents=d.get("dependents", []),
            flagged=d.get("flagged", []),
            changed_since_last=d.get("changed_since_last", []),
            decisions=d.get("decisions", []),
        )
