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
- **`pyright`** — type gate: reject only *new* type errors vs. the pre-edit baseline (like the lint gate; absolute "must be type-perfect" over-rejects behavior-correct code).
- **`pytest`** — behavior gate: type-clean ≠ behavior-preserving. This is what catches silent regressions and proves a dead-code removal or duplicate merge is safe.

## Duplicate & Dead-Code Analysis

- **Structural fingerprinting** — normalize each function's AST (strip identifiers and literals, keep structural shape), hash it, and key it in Redis. Catches exact and near-exact clones precisely and cheaply.
- **Hybrid search (vector ⊕ lexical)** — embed the *actual* function (signature + body + docstring) via an embedding model, index it in Redis (via **RedisVL**) alongside a BM25 text field and tag filters, and query with **`FT.HYBRID`** — fusing semantic vector similarity with BM25 keyword match (RRF by default). Pure embeddings are weak on code (they miss exact identifiers/API names); hybrid catches both "same logic, different names" (vector) and "shared identifiers" (BM25). Catches same-intent / different-structure duplicates that structural hashing misses. (See [05-redis-iris.md](05-redis-iris.md) for the full design.)
- **Call-graph reachability** — a directed symbol graph over the full AST (nodes: functions/classes/module assignments; edges: call + import references). Entry points (public API exports, `__main__`, direct test callees) are marked; BFS/DFS reachability from those anchors flags unreachable symbols as dead-code candidates with a confidence score.

## Memory & State — Redis Iris

**Redis is the primary backend (accessed via RedisVL), with a mandatory local-file fallback so the demo always runs offline.** Redis Iris is used as four components — full detail in [05-redis-iris.md](05-redis-iris.md):

- **LangCache / AST-keyed cache** — memoizes analysis + classification keyed on a *normalized AST signature* (exact key, not fuzzy match — so a re-seen file skips re-parsing without risking a false cache hit corrupting accuracy).
- **Hybrid Search Index** — per-function docs (vector embedding + BM25 text + tag filters), queried with `FT.HYBRID` (vector ⊕ BM25, RRF-fused) for `find_duplicates` and context retrieval. Needs Redis 8.4+ with the Query Engine (Redis Cloud / Redis Stack); falls back to brute-force vector scan otherwise.
- **Agent Memory (long-term tier)** — persists module context, architectural decisions, and refactor history *across sessions* so knowledge compounds instead of being re-derived each run.
- **Context Retriever** — structured `Tag`/`Num` filters **+** hybrid retrieval ("the 3 most relevant prior context entries for this module" by names *and* meaning) that feed Claude's next proposal without loading the whole repo.

**Fallback:** every Iris component degrades to a local `.refactorika/` file (`state.json`, plus `context/<module>.md` for docs). If Redis is unreachable the harness still works — Redis is an optimization and the live demo's visualization (Redis Insight), never a hard dependency.

## Embeddings

- **`text-embedding-3-small`** via OpenAI (`OPENAI_API_KEY`) as the primary provider; **`sentence-transformers`** (`all-MiniLM-L6-v2`, local/offline, no key) as the keyless fallback. Indexed + searched via **RedisVL** (`redisvl`). Shipped as an **optional extra** (`refactorika[semantic]`, which pulls `redisvl` + `openai`/`sentence-transformers`) — without it, duplicate detection runs structural-only.

## Testing

- **`pytest`** — unit + integration tests for the analysis, transforms, gate stack, and storage fallback.
</content>
