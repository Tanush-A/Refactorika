# Tech Stack

<<<<<<< HEAD
Refactorika itself is written in **Python**; its target codebases are also **Python**.
=======
## Language
>>>>>>> c96dee28d47b378d45255520cb4702fd3e74059a

- **Python 3.11+** — the harness *and* the target it refactors.

<<<<<<< HEAD
## Delivery / integration layer
- **MCP server** — the primary delivery form. Exposes tools (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `run_lint`, `run_tests`, `record_edit`) so Refactorika plugs into existing MCP-compatible agents (Claude Code, Cursor, etc.) as a refactor plugin, rather than shipping as a standalone IDE.
- **CLI fallback** — `refactorika audit <repo>`, `refactorika plan`, `refactorika check <diff>` — works directly against git history/diffs without a live agent loop wired up.
=======
## MCP Layer
>>>>>>> c96dee28d47b378d45255520cb4702fd3e74059a

- **`mcp` Python SDK (`FastMCP`)** — exposes Refactorika's capabilities as MCP tools Claude invokes inline during a conversation. The server (`refactorika/mcp_server.py`) is a thin shell over the core library.

## Code Analysis

- **`tree-sitter` + `tree-sitter-python`** — AST parsing for all structure-aware analysis: function boundaries, import blocks, nesting depth, normalized structural fingerprints, and the symbol graph used for dead-code reachability.

<<<<<<< HEAD
- Tree-sitter + grep over a full type-resolver because v1 explicitly doesn't promise IDE-grade accuracy — it's framed honestly as best-effort (see [08-risks-and-scope.md](08-risks-and-scope.md)). (`pyright` is used only as a pass/fail gate on edits, not as the audit's detection engine.)
- MCP-first because the explicit positioning is "plugin for existing agent loops," not a standalone product — see [01-problem-and-purpose.md](01-problem-and-purpose.md).
- Redis Iris is chosen because its actual components (Agent Memory, Context Retriever, structured caching) map directly onto Refactorika's existing mechanism (a retrievable rule list + structured call-site lookups), rather than being bolted on for a sponsor track.
=======
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

## Embeddings

- **`sentence-transformers`** (`all-MiniLM-L6-v2`, local/offline, no key) by default; **`text-embedding-3-small`** via OpenAI when `OPENAI_API_KEY` is set. Shipped as an **optional extra** (`refactorika[semantic]`) — without it, duplicate detection runs structural-only. Cosine math via `numpy`.

## Testing

- **`pytest`** — unit + integration tests for the analysis, transforms, gate stack, and storage fallback.
</content>
>>>>>>> c96dee28d47b378d45255520cb4702fd3e74059a
