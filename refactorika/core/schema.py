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
    # v3 pipeline kinds (driven by the graph + deterministic engines)
    "rename",
    "move",
    "extract",
    "inline",
    "change_signature",
    "decompose_function",
    "cleanup",
)

# v3 transform kinds the deterministic engines know how to apply from a TransformSpec.
TRANSFORM_KINDS = (
    "rename",            # reference-correct symbol rename across the repo
    "move",              # move a symbol to another module, fix imports
    "extract",           # extract a block/helper
    "inline",            # inline a symbol into callers
    "change_signature",  # change params/return, update call sites
    "decompose_function",# split a god function into named pieces (LLM body, AST-node replace)
    "cleanup",           # ruff + autoflake deterministic cleanup
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


# ---------------------------------------------------------------------------
# V3 pipeline types — the contracts the Scout/Planner/Refactor/Checker loop runs on
# ---------------------------------------------------------------------------

@dataclass
class ScoutReport:
    """One file's read-only findings from the parallel Scout pass."""

    module: str
    file: str
    summary: str = ""
    smells: list[str] = field(default_factory=list)  # human-readable smell notes
    symbols: list[str] = field(default_factory=list)  # qualnames defined here

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ScoutReport":
        return cls(**d)


@dataclass
class TransformSpec:
    """A unit of work the LLM emits as *parameters*, not a diff.

    The deterministic engine for `kind` reads `params` and applies the change
    reference-correctly. `target` is the primary symbol qualname the change acts on
    (used for ordering and impact analysis).
    """

    kind: str  # one of TRANSFORM_KINDS
    target: str  # primary symbol qualname this transform acts on
    params: dict = field(default_factory=dict)  # e.g. {"new_name": "..."} for rename
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TransformSpec":
        return cls(
            kind=d["kind"],
            target=d.get("target", ""),
            params=d.get("params", {}),
            rationale=d.get("rationale", ""),
        )


@dataclass
class PlanItem:
    """One entry in the ordered worklist: a transform plus its position/impact."""

    spec: TransformSpec
    order_index: int  # leaf-to-root position
    impact: list[str] = field(default_factory=list)  # qualnames to re-verify after

    def to_dict(self) -> dict:
        return {
            "spec": self.spec.to_dict(),
            "order_index": self.order_index,
            "impact": self.impact,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlanItem":
        return cls(
            spec=TransformSpec.from_dict(d["spec"]),
            order_index=d.get("order_index", 0),
            impact=d.get("impact", []),
        )


@dataclass
class Worklist:
    """The planner's ordered output: leaf-to-root transform specs + reported cycles."""

    items: list[PlanItem] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"items": [i.to_dict() for i in self.items], "cycles": self.cycles}

    @classmethod
    def from_dict(cls, d: dict) -> "Worklist":
        return cls(
            items=[PlanItem.from_dict(i) for i in d.get("items", [])],
            cycles=d.get("cycles", []),
        )


@dataclass
class RefactorDecision:
    """A recorded choice, written to memory so later nodes stay consistent.

    Example: extracting a duplicate the first time records the helper name chosen, so
    the second near-duplicate is consolidated under the *same* name.
    """

    pattern: str  # what kind of situation (e.g. "duplicate-discount-logic")
    transform_kind: str
    target: str
    choice: dict = field(default_factory=dict)  # e.g. {"helper_name": "apply_discount"}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RefactorDecision":
        return cls(**d)


@dataclass
class PipelineResult:
    """The end-to-end outcome of a run: every edit + before/after metrics."""

    path: str
    records: list[dict] = field(default_factory=list)  # EditRecord.to_dict() each
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    cycles: list[list[str]] = field(default_factory=list)
    applied: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
