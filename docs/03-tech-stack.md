# Tech Stack

## Language

- **Python 3.11+** — the harness *and* the target it refactors.

## MCP Layer

- **`mcp` Python SDK (`FastMCP`)** — exposes Refactorika's capabilities as MCP tools Claude invokes inline during a conversation. The server (`refactorika/mcp_server.py`) is a thin shell over the core library.

## Code Analysis

- **`tree-sitter` + `tree-sitter-python`** — AST parsing for all structure-aware analysis: function boundaries, import blocks, nesting depth, normalized structural fingerprints, and the symbol graph used for dead-code reachability.

## Verification Gate Stack

Every mutation passes these in cheapest-first order, short-circuiting on the first failure (see [04-architecture.md](04-architecture.md)):

- **`tree-sitter`** — parse gate: the edited source must parse with no `ERROR`/`MISSING` nodes.
- **`ruff`** — lint/format gate: normalize formatting, then reject only *new* violations vs. the pre-edit baseline.
- **`pyright`** — type gate: refactored output must stay type-safe (zero errors).
- **`pytest`** — behavior gate: type-clean ≠ behavior-preserving. This is what catches silent regressions and proves a dead-code removal or duplicate merge is safe.

## Duplicate & Dead-Code Analysis

- **Structural fingerprinting** — normalize each function's AST (strip identifiers and literals, keep structural shape), hash it, and key it in Redis. Catches exact and near-exact clones precisely and cheaply.
- **Semantic embeddings** — embed the *actual* function (signature + body + docstring), not just the denatured shape, via an embedding model. Default **`sentence-transformers`** (local, offline-capable, no API key); optional **`text-embedding-3-small`** via OpenAI for higher quality when an API key is configured. Stored in the Redis vector index and queried by cosine similarity. Catches same-intent / different-structure duplicates that structural hashing misses. (See [05-redis-iris.md](05-redis-iris.md) for why both tiers exist.)
- **Call-graph reachability** — a directed symbol graph over the full AST (nodes: functions/classes/module assignments; edges: call + import references). Entry points (public API exports, `__main__`, direct test callees) are marked; BFS/DFS reachability from those anchors flags unreachable symbols as dead-code candidates with a confidence score.

## Memory & State — Redis Iris

**Redis is the primary backend, with a mandatory local-file fallback so the demo always runs offline.** Redis Iris is used as four components — full detail in [05-redis-iris.md](05-redis-iris.md):

- **LangCache / AST-keyed cache** — memoizes analysis + classification keyed on a *normalized AST signature* (exact key, not fuzzy match — so a re-seen file skips re-parsing without risking a false cache hit corrupting accuracy).
- **Vector Index** — per-function embeddings keyed on `{file}:{function_name}`, queried by cosine similarity for `find_duplicates` and for context retrieval.
- **Agent Memory (long-term tier)** — persists module context, architectural decisions, and refactor history *across sessions* so knowledge compounds instead of being re-derived each run.
- **Context Retriever** — structured lookups (call sites, conventions) **+** vector lookups ("the 3 most relevant prior context entries for this module") that feed Claude's next proposal without loading the whole repo.

**Fallback:** every Iris component degrades to a local `.refactorika/` file (`state.json`, plus `context/<module>.md` for docs). If Redis is unreachable the harness still works — Redis is an optimization and the live demo's visualization (Redis Insight), never a hard dependency.

## Testing

- **`pytest`** — unit + integration tests for the analysis, transforms, gate stack, and storage fallback.
</content>
