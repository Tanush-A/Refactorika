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

- **Redis / Redis Iris** — primary storage for refactoring results and analysis state. v1 uses Redis as a cache keyed by file content hash. v2 expands to four Iris components:
  - **Agent Memory (long-term tier)** — persists codebase knowledge cross-session (architectural context, prior refactor history); v1 is within-run only.
  - **Vector Index** — stores per-function embeddings keyed on `{file}:{function_name}`; queried by cosine similarity to surface semantic duplicates and retrieve relevant context during documentation generation.
  - **Context Retriever** — structured + vector-based lookups backing `find_duplicates` and `generate_docs`; pulls the most relevant prior context for a given module without loading the full repo.
  - **LangCache** — caches repeated classification calls keyed on normalized AST signature (not semantic similarity, to avoid false cache hits corrupting analysis accuracy).
- **Local JSON fallback** — `cache.py` falls back to a local `.refactorika/cache.json` when Redis is unreachable; every external call has a hardcoded offline path so the demo runs without a live Redis instance.

## v2 Analysis

- **Embedding model** (`text-embedding-3-small` via OpenAI, or `sentence-transformers` for local/offline) — embeds normalized AST signatures of functions and modules for vector similarity search.
- **Graph-based repo mapper** — builds a directed call graph over the full AST (nodes: symbols; edges: call/import references). Entry points (public API exports, `__main__`, direct test callees) are marked; BFS/DFS reachability from those anchors identifies unreachable symbols as dead code candidates.

## Testing

- **`pytest`** — unit and integration tests for refactoring transformations
