"""Thin MCP shell. Claude proposes; Refactorika verifies. Tools wrap the core 1:1."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .analysis.dead_code import find_dead_code as _find_dead_code
from .analysis.duplicates import find_duplicates as _find_duplicates
from .core.analyze import analyze_file as _analyze_file
from .core.apply import apply_and_verify as _apply_and_verify
from .core.apply import apply_and_verify_multi as _apply_and_verify_multi
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
def get_log() -> list[dict]:
    """Return the append-only edit log (powers the dashboard)."""
    return _storage.get_log()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
