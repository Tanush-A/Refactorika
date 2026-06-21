# v2 Features — Redis-Advanced Capabilities

Refactorika v2 expands beyond structural refactoring to address two problems that grow as codebases scale: **context and documentation rot**, and **duplicate and dead code accumulation**. Both are powered by Redis Iris going beyond caching — using agent memory, vector search, and context retrieval as first-class components.

## Problem 1: Context & Documentation Rot

### The knowledge gap

Codebases grow horizontally. Engineers leave, and the intent behind architectural decisions vanishes. Documentation is almost always outdated because humans hate writing it and forget to update it when logic changes. Developers spend days playing "software archaeologist" — using `git blame` to figure out why a bizarre workaround was added three years ago, terrified that changing it will break a silent dependency.

### How v2 tackles it

**`generate_docs(path)`** — AI parses the target file or directory and emits a structured `.refactorika/context/<module>.md` capturing:
- Module purpose and design intent.
- Key exported symbols and their dependents.
- Architectural decisions embedded in the code (unusual patterns, workarounds, invariants).

The output is self-updating: running `generate_docs` again after a refactor diffs the new context against the old one and records only what changed. Onboarding engineers read these files instead of `git blame`.

**Redis role — cross-session Agent Memory:**
- v1 agent memory lasted only within a single run. v2 persists to Redis long-term tier across sessions.
- Each `generate_docs` call stores the module's context entry in Agent Memory keyed by file path.
- Subsequent calls retrieve prior context (what was documented last time, what changed) via the Context Retriever — enabling incremental updates rather than full regeneration.
- Over time, codebase knowledge accumulates: repeated runs on the same repo build a richer context map without re-deriving structure from scratch.

## Problem 2: Duplicate & Dead Code

### The phantom duplicate problem

When large codebases scale, individual developers lose sight of the global architecture. If an engineer needs a specific utility (a date-formatter, a custom UI card), they often won't realize a coworker already built it in a different directory. They write their own version. Over time, codebases accumulate mass amounts of dead code and phantom/duplicate code — the same logic written five different ways.

The consequence: fixing a bug in one copy doesn't fix it in the other four. Bundle size grows, performance degrades, and the codebase becomes progressively harder to reason about.

### How v2 tackles it

#### Duplicate detection — `find_duplicates(path)`

1. Parse each function in the target path with `tree-sitter-python` and normalize the AST signature (strip variable names and literals, keep structural shape).
2. Embed the normalized signature via the embedding model (`text-embedding-3-small` or `sentence-transformers`).
3. Store embeddings in the Redis Vector Index keyed on `{file}:{function_name}`.
4. Query by cosine similarity above a configurable threshold to surface semantically similar function pairs.
5. Return a ranked list of duplicate pairs with: file locations, similarity score, and a suggested consolidation target (the function with more call sites, or the one in the more central module).

This catches **semantic** duplicates — same logic, different variable names or slightly different structure — that exact-match tools (grep, AST equality) would miss.

#### Dead code analysis — `find_dead_code(path)`

1. Build a directed call graph over the full AST: nodes are symbols (functions, classes, module-level assignments); edges are call and import references.
2. Mark entry points: public API exports (anything in `__all__` or without a leading `_`), `__main__` blocks, and direct test callees.
3. BFS/DFS reachability from those anchors; any symbol not reachable is a dead code candidate.
4. Assign a confidence score: high confidence for private functions with no references; lower confidence for public symbols that may be called via dynamic dispatch or external packages.
5. Return the dead code report with symbol locations and confidence scores. Never auto-delete — always surface to the developer for confirmation.

## Redis Iris v2 Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Redis Iris (v2)                       │
│                                                          │
│  ┌─────────────────┐   ┌──────────────────────────────┐ │
│  │  Agent Memory   │   │       Vector Index           │ │
│  │  (long-term)    │   │  {file}:{fn} → embedding     │ │
│  │                 │   │  cosine similarity queries   │ │
│  │  • module docs  │   │  for duplicate detection     │ │
│  │  • arch context │   │  and context retrieval       │ │
│  │  • refactor log │   └──────────────────────────────┘ │
│  │  cross-session  │                                     │
│  └─────────────────┘   ┌──────────────────────────────┐ │
│                         │    Context Retriever (v2)    │ │
│  ┌─────────────────┐   │                              │ │
│  │   LangCache     │   │  structured: call-site,      │ │
│  │                 │   │  convention lookups (v1)     │ │
│  │  AST-keyed      │   │  + vector: "find 3 most      │ │
│  │  classification │   │  relevant prior entries      │ │
│  │  cache (v1+v2)  │   │  for this module" (v2)       │ │
│  └─────────────────┘   └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

**Local JSON fallback:** all four Iris components have a local `.refactorika/` file fallback — the demo runs offline; Redis is preferred for the demo's live visualization in Redis Insight.

## v2 Build Order

1. **Duplicate detection first** — highest demo impact, builds directly on existing tree-sitter AST work. Add embedding generation + Redis vector index.
2. **Cross-session agent memory** — upgrade the existing storage layer; extends naturally from v1's within-run memory.
3. **Documentation generation** — depends on cross-session memory being in place (so prior context is retrievable on incremental runs).
4. **Dead code analysis** — graph-based; independent of the embedding pipeline; can be parallelized with step 2.

## Demo additions (v2)

- **Duplicate pair caught live**: run `find_duplicates` on a curated repo with a planted semantic duplicate → Redis Insight shows the vector index populated → similarity query returns the duplicate pair with score.
- **Dead code report**: run `find_dead_code` → show the call graph → flag unreachable private function with high confidence.
- **Living docs**: run `generate_docs` before and after a refactor → show the diff of the context file → Redis Insight shows the agent memory entry updating.
