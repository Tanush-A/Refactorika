# Redis Iris — The Memory Layer

Most refactoring tools are stateless: every run re-parses the world from scratch and forgets everything the moment it finishes. Refactorika's differentiator is **memory** — knowledge about a codebase compounds across runs and across sessions. That memory layer is **Redis Iris**, used not as a dumb cache but as four cooperating components: an AST-keyed cache, a vector index, long-term agent memory, and a context retriever.

Redis is the **primary** backend. Every component degrades to a local `.refactorika/` file so the harness — and the demo — runs fully offline. Redis is what makes it fast, persistent, and *visualizable* (Redis Insight during the demo); it is never a hard dependency.

```
┌──────────────────────────────────────────────────────────────────┐
│                         Redis Iris                                 │
│                                                                    │
│  ┌─────────────────────┐      ┌──────────────────────────────┐    │
│  │  LangCache /         │      │       Vector Index            │    │
│  │  AST-keyed cache     │      │  {file}:{fn} → embedding      │    │
│  │                      │      │  cosine-similarity queries    │    │
│  │  • analysis results  │      │  • semantic duplicate pairs   │    │
│  │  • classifications   │      │  • relevant-context retrieval │    │
│  │  exact key, no fuzzy │      └──────────────────────────────┘    │
│  └─────────────────────┘                                           │
│  ┌─────────────────────┐      ┌──────────────────────────────┐    │
│  │  Agent Memory        │      │     Context Retriever         │    │
│  │  (long-term tier)    │      │                               │    │
│  │  • module context    │  ←→  │  structured: call-site,       │    │
│  │  • arch decisions     │      │  convention lookups           │    │
│  │  • refactor history   │      │  + vector: "3 most relevant   │    │
│  │  cross-session        │      │  prior entries for this mod"  │    │
│  └─────────────────────┘      └──────────────────────────────┘    │
│                                                                    │
│  Local fallback: .refactorika/state.json · context/<module>.md     │
└──────────────────────────────────────────────────────────────────┘
```

## 1. LangCache / AST-keyed cache

**Job:** never analyze the same code twice. Analysis and classification results are memoized keyed on a **normalized AST signature** (a hash of the structure, computed by tree-sitter) — so a re-seen file skips re-parsing entirely.

**Why exact-key, not semantic match:** LangCache normally matches on semantic similarity, but for *analysis* that's a hazard — a fuzzy hit would hand back the wrong file's smells and corrupt accuracy. We deliberately key on the exact normalized signature: a cache hit means *structurally identical input*, so the cached result is guaranteed correct. Same speed win, zero correctness risk.

**Fallback:** the `cache` map in `.refactorika/state.json`.

## 2. Vector Index

**Job:** find functions that *do the same thing* even when they don't *look* the same. Each function is embedded and stored keyed on `{file}:{function_name}`; `find_duplicates` queries by cosine similarity above a configurable threshold.

This is the home of the **two-tier duplicate detection** that resolves the "structural vs. semantic" question:

- **Tier 1 — structural (cheap, precise).** Normalize the AST (strip identifiers + literals, keep shape), hash it, compare hashes. Catches exact and near-exact clones with zero false positives. This is *not* the vector index — it's the AST-keyed cache doing fingerprint comparison.
- **Tier 2 — semantic (the vector index).** Embed the **actual function** — signature + body + docstring, *not* the denatured shape — and compare vectors. This is what catches "same logic, different structure/names" that hashing provably cannot, because hashing only sees shape and these duplicates differ in shape.

Tier 1 alone is just AST equality; tier 2 alone is fuzzy and expensive. Running both gives precision *and* recall, and gives the vector index an honest, non-overlapping job. The same index doubles as the semantic backbone of the Context Retriever (component 4).

**Embeddings:** default **`sentence-transformers`** (local, offline, no key); optional **`text-embedding-3-small`** via OpenAI when `OPENAI_API_KEY` is set, for higher-quality vectors. The offline default keeps the demo self-contained.

**Fallback:** a brute-force cosine scan over embeddings persisted in the local JSON — slower, identical results.

## 3. Agent Memory (long-term tier)

**Job:** make the harness smarter every session instead of starting from zero. This is the upgrade from a within-run scratchpad to **cross-session persistence**:

- **Module context** — what `generate_docs` produced last time (purpose, exports, dependents, decisions), keyed by file path.
- **Architectural decisions** — the unusual patterns, workarounds, and invariants captured from the code, so the *why* survives team turnover.
- **Refactor history** — the `EditRecord` log: what was tried, what passed/failed each gate, what was rolled back. Prevents re-proposing an edit that already failed verification.

Because it persists, the second run on a repo retrieves prior context and does **incremental** work (diff against last time) rather than full regeneration. Repeated runs build a richer knowledge map without re-deriving structure.

**Fallback:** `log` + `context` entries in `.refactorika/state.json` and `.refactorika/context/<module>.md`.

## 4. Context Retriever

**Job:** feed Claude exactly the relevant prior knowledge for the task at hand — without loading the whole repo into context. It combines two lookup modes:

- **Structured** — direct lookups: call sites of a symbol, project import conventions, the dependents of a module.
- **Vector** — semantic retrieval over the vector index: "the 3 most relevant prior context entries for *this* module," "functions similar to the one Claude is about to extract."

This is what powers incremental `generate_docs` (retrieve last context → diff → update only what changed) and gives `apply_and_verify` proposals grounding in established conventions, so a refactor matches the surrounding code instead of inventing a new style.

**Fallback:** structured lookups run directly over the AST; vector lookups use the brute-force cosine scan from component 2.

## How the components serve each tool

| Tool | Primary Iris components |
|---|---|
| `analyze_file` | LangCache (skip re-parse) |
| `find_duplicates` | AST-keyed cache (tier 1) + Vector Index (tier 2) |
| `find_dead_code` | Agent Memory (prior call-graph), Context Retriever (dependents) |
| `generate_docs` | Agent Memory (prior context, incremental diff) + Context Retriever |
| `get_context_map` | Agent Memory + Context Retriever |
| `apply_and_verify` | Context Retriever (conventions) → writes refactor history to Agent Memory |

## Demo moments (Redis Insight makes the memory visible)

- **Duplicate caught live** — run `find_duplicates` on a curated repo with a planted semantic duplicate → Redis Insight shows the vector index populating → the similarity query returns the pair with a score and a consolidation target → Claude proposes the merge → `apply_and_verify` proves it safe and commits.
- **Dead code, safely removed** — `find_dead_code` flags an unreachable private function with high confidence → Claude proposes the deletion → `pytest` proves nothing breaks → committed. The removal is *verified*, not blind.
- **Living docs** — run `generate_docs` before and after a refactor → show the diff of the context file → Redis Insight shows the agent-memory entry updating in place.
- **Memory compounds** — run the harness twice; the second run hits the AST cache and retrieves prior context instead of re-deriving it — visibly faster, visibly remembering.
</content>
