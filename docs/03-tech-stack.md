# Tech Stack

## Language

- **Python 3.11+** — primary implementation language

## MCP Layer

- **`mcp` Python SDK** — exposes Refactorika's refactoring capabilities as MCP tools that Claude can invoke directly during a conversation

## Code Analysis

- **`tree-sitter`** + **`tree-sitter-python`** — AST parsing for structure-aware analysis (function boundaries, import blocks, nesting depth, etc.)

## Static Analysis & Linting

- **`pyright`** — type checking; used to validate that refactored output is type-safe
- **`ruff`** — linting and formatting; used to normalize output and catch style regressions after refactoring

## Caching & State

- **Redis** — caching for refactoring results and intermediate analysis state; enables fast re-analysis of previously seen files without re-parsing

## Testing

- **`pytest`** — unit and integration tests for refactoring transformations
