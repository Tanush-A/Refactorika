# Redis Iris — Caching & Embeddings

Refactorika uses **Redis Iris** as two cooperating within-session components: an AST-keyed cache and a vector index for semantic duplicate detection. There is no cross-session persistence — each run is self-contained.

Redis is the **primary** backend. Every component degrades to a local `.refactorika/` file so the harness — and the demo — runs fully offline. Redis is what makes it fast and *visualizable* (Redis Insight during the demo); it is never a hard dependency.

```
┌──────────────────────────────────────────────────────────────────┐
│                         Redis Iris                                 │
│                                                                    │
│  ┌─────────────────────┐      ┌──────────────────────────────┐    │
│  │  LangCache /         │      │       Vector Index            │    │
│  │  AST-keyed cache     │      │  {file}:{fn} → embedding      │    │
│  │                      │      │  cosine-similarity queries    │    │
│  │  • analysis results  │      │  • semantic duplicate pairs   │    │
│  │  • classifications   │      │                               │    │
│  │  exact key, no fuzzy │      └──────────────────────────────┘    │
│  └─────────────────────┘                                           │
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

## How the components serve each tool

| Tool | Primary Iris components |
|---|---|
| `analyze_file` | LangCache (skip re-parse) |
| `find_duplicates` | AST-keyed cache (tier 1) + Vector Index (tier 2) |
| `find_dead_code` | LangCache (prior call-graph within session) |
| `generate_docs` | — (writes to local `.refactorika/context/<module>.md`) |
| `apply_and_verify` | LangCache (baseline for ruff diff) |

## Demo moments (Redis Insight makes caching visible)

- **Duplicate caught live** — run `find_duplicates` on a curated repo with a planted semantic duplicate → Redis Insight shows the vector index populating → the similarity query returns the pair with a score and a consolidation target → Claude proposes the merge → `apply_and_verify` proves it safe and commits.
- **Dead code, safely removed** — `find_dead_code` flags an unreachable private function with high confidence → Claude proposes the deletion → `pytest` proves nothing breaks → committed. The removal is *verified*, not blind.
- **Living docs** — run `generate_docs` after a refactor → show the generated context file capturing the module's purpose and decisions.
</content>
