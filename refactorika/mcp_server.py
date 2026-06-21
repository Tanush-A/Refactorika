"""Thin MCP shell. Claude proposes; Refactorika verifies. Tools wrap the core 1:1."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .core.analyze import analyze_file as _analyze_file
from .core.apply import apply_and_verify as _apply_and_verify
from .core.storage import Storage

mcp = FastMCP("refactorika")
_storage = Storage()


@mcp.tool()
def analyze_file(path: str) -> dict:
    """Find ranked structural-refactor opportunities in a Python file (read-only)."""
    return _analyze_file(path, _storage).to_dict()


@mcp.tool()
def apply_and_verify(path: str, new_content: str, refactor_kind: str) -> dict:
    """Apply Claude's proposed file contents, run the gate stack, commit or roll back atomically.

    Returns an EditRecord. On status 'rolled-back', read 'failure_reason' and re-propose.
    """
    return _apply_and_verify(path, new_content, refactor_kind, _storage).to_dict()


@mcp.tool()
def get_log() -> list[dict]:
    """Return the append-only edit log (powers the dashboard)."""
    return _storage.get_log()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
