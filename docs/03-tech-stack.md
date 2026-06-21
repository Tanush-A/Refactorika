# Tech Stack

<<<<<<< HEAD
Refactorika itself is written in **Python**; its target codebases are also **Python**.
=======
## Language
>>>>>>> narrow-scope-anika

- **Python 3.11+** — primary implementation language

<<<<<<< HEAD
## Delivery / integration layer
- **MCP server** — the primary delivery form. Exposes tools (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `run_lint`, `run_tests`, `record_edit`) so Refactorika plugs into existing MCP-compatible agents (Claude Code, Cursor, etc.) as a refactor plugin, rather than shipping as a standalone IDE.
- **CLI fallback** — `refactorika audit <repo>`, `refactorika plan`, `refactorika check <diff>` — works directly against git history/diffs without a live agent loop wired up.
=======
## MCP Layer
>>>>>>> narrow-scope-anika

- **`mcp` Python SDK** — exposes Refactorika's refactoring capabilities as MCP tools that Claude can invoke directly during a conversation

## Code Analysis

- **`tree-sitter`** + **`tree-sitter-python`** — AST parsing for structure-aware analysis (function boundaries, import blocks, nesting depth, etc.)

<<<<<<< HEAD
- Tree-sitter + grep over a full type-resolver because v1 explicitly doesn't promise IDE-grade accuracy — it's framed honestly as best-effort (see [08-risks-and-scope.md](08-risks-and-scope.md)). (`pyright` is used only as a pass/fail gate on edits, not as the audit's detection engine.)
- MCP-first because the explicit positioning is "plugin for existing agent loops," not a standalone product — see [01-problem-and-purpose.md](01-problem-and-purpose.md).
- Redis Iris is chosen because its actual components (Agent Memory, Context Retriever, structured caching) map directly onto Refactorika's existing mechanism (a retrievable rule list + structured call-site lookups), rather than being bolted on for a sponsor track.
=======
## Static Analysis & Linting

- **`pyright`** — type checking; used to validate that refactored output is type-safe
- **`ruff`** — linting and formatting; used to normalize output and catch style regressions after refactoring

## Caching & State

- **Redis** — caching for refactoring results and intermediate analysis state; enables fast re-analysis of previously seen files without re-parsing

## Testing

- **`pytest`** — unit and integration tests for refactoring transformations
>>>>>>> narrow-scope-anika
