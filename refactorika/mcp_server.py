"""Thin MCP shell. Claude proposes; Refactorika verifies. Tools wrap the core 1:1."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .analysis.audit import audit_repo as _audit_repo
from .analysis.audit import build_plan as _build_plan
from .analysis.dead_code import find_dead_code as _find_dead_code
from .analysis.duplicates import find_duplicates as _find_duplicates
from .analysis.related import find_related as _find_related
from .core.analyze import analyze_file as _analyze_file
from .core.apply import apply_and_verify as _apply_and_verify
from .core.apply import apply_and_verify_multi as _apply_and_verify_multi
from .core.schema import Plan
from .core.storage import Storage
from .docs_gen import generate_docs as _generate_docs
from .docs_gen import get_context_map as _get_context_map
from .memory.agent_memory import AgentMemory
from .memory.context import ContextRetriever
from .memory.vector_index import VectorIndex

mcp = FastMCP("refactorika")

_storage = Storage()
_vector_index = VectorIndex(_storage)
_agent_memory = AgentMemory(_storage)
_context_retriever = ContextRetriever(_storage, _agent_memory)


@mcp.tool()
def analyze_file(path: str) -> dict:
    """Find ranked structural-refactor opportunities in a Python file (read-only)."""
    return _analyze_file(path, _storage).to_dict()


@mcp.tool()
def find_duplicates(path: str, threshold: float = 0.83) -> dict:
    """Detect duplicate functions in a file or directory (read-only).

    Tier 1 (structural): exact-shape clones via AST fingerprint.
    Tier 2 (semantic): near-duplicates via embedding cosine similarity (requires [semantic] extra).
    Returns ranked pairs with consolidation targets. Claude proposes the merge via apply_and_verify.
    """
    return _find_duplicates(path, _storage, _vector_index, threshold)


@mcp.tool()
def find_related(path: str, symbol: str = "", k: int = 5) -> dict:
    """Impact check: what else does changing this file affect? (read-only, advisory)

    Returns (a) `related` — functions ELSEWHERE in the repo that are semantically
    similar (hybrid vector search), i.e. parallel/duplicated logic a behavior
    change here probably needs mirrored; and (b) `dependents` — modules that
    directly import/call this one (call graph). Use before refactoring to avoid
    fixing one copy and missing the others.
    """
    return _find_related(path, _storage, _vector_index, k=k, symbol=symbol)


@mcp.tool()
def find_dead_code(path: str) -> dict:
    """Detect unreachable symbols in a file or directory via call-graph reachability (read-only).

    Returns dead symbols ranked by confidence (high/medium/low).
    Claude proposes removal via apply_and_verify with refactor_kind='remove_dead_code'.
    """
    return _find_dead_code(path, _storage)


@mcp.tool()
def apply_and_verify(path: str, new_content: str, refactor_kind: str) -> dict:
    """Apply Claude's proposed file contents, run the gate stack, commit or roll back atomically.

    Returns an EditRecord. On status 'rolled-back', read 'failure_reason' and re-propose.
    """
    return _apply_and_verify(path, new_content, refactor_kind, _storage).to_dict()


@mcp.tool()
def apply_and_verify_multi(edits: dict, refactor_kind: str) -> dict:
    """Multi-file atomic apply: snapshot all → gates → one commit or restore all.

    edits: {path: new_content} for every file to touch in one atomic operation.
    Required for consolidate_duplicate (touching ≥2 files at once).
    Returns a single EditRecord covering all files.
    """
    return _apply_and_verify_multi(edits, refactor_kind, _storage).to_dict()


@mcp.tool()
def generate_docs(path: str) -> dict:
    """Extract module context and persist to agent memory + .refactorika/context/<module>.md.

    Returns a structured skeleton with purpose hint, exports, dependents, and flagged patterns.
    Incremental on second run — reports only what changed since last call.
    """
    return _generate_docs(path, _storage, _agent_memory, _context_retriever)


@mcp.tool()
def get_context_map(path: str) -> dict:
    """Return accumulated cross-session context for a module from agent memory (read-only).

    Falls back to deriving context via generate_docs on cold cache.
    """
    return _get_context_map(path, _storage, _agent_memory, _context_retriever)


@mcp.tool()
def audit_repo(path: str) -> dict:
    """Ranked repo-wide structural-opportunity report across all files (read-only, advisory).

    Aggregates per-file analysis into one report: which files, which smells, the
    headline finding. The forest-level view a human acts on before a campaign.
    """
    return _audit_repo(path, _storage).to_dict()


@mcp.tool()
def get_plan(path: str) -> dict:
    """Dependency-ordered refactor plan (fewest-dependents-first); persists it (advisory).

    Orders deviating files low-blast-radius-first so later edits land on stable
    ground. The plan is saved as the current plan for confirm_plan to gate.
    """
    return _build_plan(path, _storage).to_dict()


@mcp.tool()
def confirm_plan(decision: str = "approve", order: list[str] | None = None) -> dict:
    """Human checkpoint: approve / reject / reorder the persisted plan. Never changes code.

    decision='approve' green-lights execution; 'reject' stops it; 'reorder' (with
    order=[file,...]) overrides the dependency heuristic. Returns the updated plan.
    """
    raw = _storage.load_plan()
    if raw is None:
        return {"error": "no plan to confirm; call get_plan first"}
    plan = Plan.from_dict(raw)
    if decision == "approve":
        plan.confirmed, plan.decision = True, "approve"
    elif decision == "reject":
        plan.confirmed, plan.decision = False, "reject"
    elif decision == "reorder" and order:
        by_file = {t.file: t for t in plan.tasks}
        plan.tasks = [by_file[f] for f in order if f in by_file]
        for i, t in enumerate(plan.tasks):
            t.order = i
        plan.confirmed, plan.decision = True, "reorder"
    _storage.save_plan(plan.to_dict())
    return plan.to_dict()


@mcp.tool()
def get_log() -> list[dict]:
    """Return the append-only edit log (powers the dashboard)."""
    return _storage.get_log()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
