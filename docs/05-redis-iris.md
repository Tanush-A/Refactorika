# Redis Iris — The Memory Layer

Most refactoring tools are stateless: every run re-parses the world from scratch and forgets everything the moment it finishes. Refactorika's differentiator is **memory** — knowledge about a codebase compounds across runs and across sessions. That memory layer is **Redis Iris**, used not as a dumb cache but as four cooperating components: an AST-keyed cache, a **hybrid (vector + lexical) search index**, long-term agent memory, and a context retriever.

Redis is the **primary** backend, accessed through **RedisVL** (the AI-native Redis client). Every component degrades to a local `.refactorika/` file so the harness — and the demo — runs fully offline. Redis is what makes it fast, persistent, *and properly searchable* (and *visualizable* in Redis Insight during the demo); it is never a hard dependency.

> **The hybrid search engine needs Redis 8.4+ with the Query Engine** (Redis Cloud, Redis Stack, or Redis 8 OSS) — a bare `redis-server` has no `FT.*`/`FT.HYBRID`. When the Query Engine is absent, the index degrades to a brute-force scan with identical (slower) results.

```
┌──────────────────────────────────────────────────────────────────┐
│                    Redis Iris  (via RedisVL)                       │
│                                                                    │
│  ┌─────────────────────┐      ┌──────────────────────────────┐    │
│  │  LangCache /         │      │   Hybrid Search Index         │    │
│  │  AST-keyed cache     │      │   per fn: vector + text + tags│    │
│  │                      │      │   FT.HYBRID: BM25 ⊕ vector    │    │
│  │  • analysis results  │      │   fused (RRF / linear)        │    │
│  │  • classifications   │      │   • duplicate pairs           │    │
│  │  exact key, no fuzzy │      │   • relevant-context retrieval│    │
│  └─────────────────────┘      └──────────────────────────────┘    │
│  ┌─────────────────────┐      ┌──────────────────────────────┐    │
│  │  Agent Memory        │      │     Context Retriever         │    │
│  │  (long-term tier)    │      │                               │    │
│  │  • module context    │  ←→  │  structured filters (tag/num) │    │
│  │  • arch decisions     │      │  fused with hybrid retrieval  │    │
│  │  • refactor history   │      │  "3 most relevant for this    │    │
│  │  cross-session        │      │   module" — meaning + names   │    │
│  └─────────────────────┘      └──────────────────────────────┘    │
│                                                                    │
│  Local fallback: .refactorika/state.json · context/<module>.md     │
└──────────────────────────────────────────────────────────────────┘
```

## 1. LangCache / AST-keyed cache

**Job:** never analyze the same code twice. Analysis and classification results are memoized keyed on a **normalized AST signature** (a hash of the structure, computed by tree-sitter) — so a re-seen file skips re-parsing entirely.

**Why exact-key, not semantic match:** LangCache normally matches on semantic similarity, but for *analysis* that's a hazard — a fuzzy hit would hand back the wrong file's smells and corrupt accuracy. We deliberately key on the exact normalized signature: a cache hit means *structurally identical input*, so the cached result is guaranteed correct. Same speed win, zero correctness risk.

**Fallback:** the `cache` map in `.refactorika/state.json`.

## 2. Hybrid Search Index (vector + lexical)

**Job:** find functions that *do the same thing* even when they don't *look* the same — without the false positives pure semantic search produces on code. Each function is indexed in Redis (via RedisVL) as a document with **three** field types:
- a **vector** field — the OpenAI embedding of the function (HNSW, cosine);
- a **text** field — the function's signature + body + identifiers, BM25-scored;
- **tag/numeric** fields — `file`, `module`, arity, and the normalized AST fingerprint, for exact filtering.

`find_duplicates` runs a **hybrid query** (`FT.HYBRID`): BM25 lexical match *and* vector similarity in one Redis call, fused into a single ranked list (**RRF** by default, or linear weighting).

**Why hybrid, not pure vector — this is the whole point.** Semantic-only embeddings are genuinely weak on *code*: two unrelated helpers can be cosine-close, and embeddings "struggle with precise identifiers like API names, function names, and technical terminology that need exact matching" (Redis). Lexical-only (BM25) misses "same logic, different names." Fusing them is strictly better — Redis reports hybrid retrieval lifts recall **3–3.5×** and end-to-end accuracy **+11–15%** vs. single-mode. For us: the vector half catches renamed-but-equivalent logic; the BM25 half anchors on shared identifiers/call names; RRF blends them.

This pairs with the structural tier to give **three signals**, not one:
- **Tier 1 — structural fingerprint (AST-keyed cache).** Strip identifiers + literals, hash the shape, compare hashes. Exact/near-exact clones, zero false positives. (This is the cache doing fingerprint comparison, *not* the search index.)
- **Tier 2 — hybrid (this index).** Vector (meaning) ⊕ BM25 (identifiers), fused — catches what structural hashing provably can't.

The same hybrid index is the retrieval backbone of the Context Retriever (component 4).

**Embeddings:** **`text-embedding-3-small`** via OpenAI (`OPENAI_API_KEY`) as the primary provider; **`sentence-transformers`** (local, offline, no key) as the keyless fallback. Both shipped behind the `[semantic]` extra.

**Fallback:** when no Query Engine is reachable, a brute-force cosine scan over embeddings in local JSON — slower, vector-only (no BM25 fusion), same correctness floor.

## 3. Agent Memory (long-term tier)

**Job:** make the harness smarter every session instead of starting from zero. This is the upgrade from a within-run scratchpad to **cross-session persistence**:

- **Module context** — what `generate_docs` produced last time (purpose, exports, dependents, decisions), keyed by file path.
- **Architectural decisions** — the unusual patterns, workarounds, and invariants captured from the code, so the *why* survives team turnover.
- **Refactor history** — the `EditRecord` log: what was tried, what passed/failed each gate, what was rolled back. Prevents re-proposing an edit that already failed verification.

Because it persists, the second run on a repo retrieves prior context and does **incremental** work (diff against last time) rather than full regeneration. Repeated runs build a richer knowledge map without re-deriving structure.

**Fallback:** `log` + `context` entries in `.refactorika/state.json` and `.refactorika/context/<module>.md`.

## 4. Context Retriever

**Job:** feed Claude exactly the relevant prior knowledge for the task at hand — without loading the whole repo into context. It runs the same **hybrid retrieval** as component 2, with structured filters layered on:

- **Hybrid** — "the 3 most relevant prior context entries for *this* module" via BM25 (module/symbol names) ⊕ vector (context-summary meaning), fused with RRF. Names *and* meaning, not either alone.
- **Structured filters** — RedisVL `Tag`/`Num` filters narrow the hybrid query by `file`/`module`/dependents before fusion, so retrieval is scoped, not repo-wide.

This is what powers incremental `generate_docs` (retrieve last context → diff → update only what changed) and grounds `apply_and_verify` proposals in established conventions, so a refactor matches the surrounding code instead of inventing a new style.

**Fallback:** structured lookups run directly over the AST/call-graph; hybrid retrieval degrades to the brute-force vector scan from component 2 (lexical signal dropped).

## How the components serve each tool

| Tool | Primary Iris components |
|---|---|
| `analyze_file` | LangCache (skip re-parse) |
| `find_duplicates` | AST-keyed cache (tier 1) + Hybrid Search Index (tier 2: BM25 ⊕ vector) |
| `find_dead_code` | Agent Memory (prior call-graph), Context Retriever (dependents) |
| `generate_docs` | Agent Memory (prior context, incremental diff) + Context Retriever |
| `get_context_map` | Agent Memory + Context Retriever |
| `apply_and_verify` | Context Retriever (conventions) → writes refactor history to Agent Memory |

## Implementation notes (RedisVL)

- **Client:** `redisvl` (the AI-native Redis client) — defines the index `SearchIndex.from_dict(schema)` and runs hybrid queries via `HybridQuery(text=…, text_field_name=…, vector=…, vector_field_name=…, combination_method="RRF", text_scorer="BM25STD", filter_expression=Tag("module")==…)`.
- **Schema (per function doc):** `tag` fields `file`/`module`/`fingerprint`, a `text` field `body` (BM25STD), a `vector` field `embedding` (HNSW, cosine, dims = provider's). Namespace the index by `{provider}:{dim}` so a provider switch can't mix dimensions.
- **Versions:** `HybridQuery` (FT.HYBRID) needs Redis ≥ 8.4 + redis-py ≥ 7.1 (Redis Cloud qualifies). On older/keyless setups, fall back to the brute-force vector scan.
- **Fusion default:** **RRF** (no tuning, balanced); switch to linear with an alpha weight only if one signal should dominate.

## Demo moments (Redis Insight makes the memory visible)

- **Duplicate caught live** — run `find_duplicates` on a curated repo with a planted semantic duplicate → Redis Insight shows the hybrid index populating → the `FT.HYBRID` query returns the pair (BM25 ⊕ vector, RRF-fused) with a score and consolidation target → Claude proposes the merge → `apply_and_verify` proves it safe and commits.
- **Dead code, safely removed** — `find_dead_code` flags an unreachable private function with high confidence → Claude proposes the deletion → `pytest` proves nothing breaks → committed. The removal is *verified*, not blind.
- **Living docs** — run `generate_docs` before and after a refactor → show the diff of the context file → Redis Insight shows the agent-memory entry updating in place.
- **Memory compounds** — run the harness twice; the second run hits the AST cache and retrieves prior context instead of re-deriving it — visibly faster, visibly remembering.
</content>
