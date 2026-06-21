# Design: Semantic Codebase Index (embeddings-only, in Redis)

> Status: proposal for review. Scope chosen: **embeddings only** (no per-file LLM purpose
> summaries yet). Companion change: **replace the `_MIN_GOD_LINES = 12` line-count gate with a
> composite complexity score.** Both are judgment-layer augmentations — the deterministic spine
> and the "engine never depends on LLM/embeddings being reachable" principle are unchanged.

## 1. Motivation

Two concrete weaknesses in the engine today:

1. **The decompose step is context-starved.** `planner_llm._decompose_prompt` sends the LLM *only
   one function's raw text* — no neighbors, no callee purposes, no module context. The expensive,
   reference-correct Jedi graph never reaches the model. So the only "semantic" refactor we do is
   really local pattern-matching.
2. **The "consistency" recall almost never fires.** `_shape_pattern` keys decisions on a SHA of the
   canonical type stream, i.e. it only matches *structurally identical* functions. On real code,
   near-duplicates (same job, different names/shape) never collide, so naming never gets reused.

A semantic index of the codebase — every symbol embedded, queryable by similarity, stored in
Redis alongside the exact dependency graph — fixes both: it gives the LLM real neighbor context
and broadens "we've seen this before" from *identical* to *similar*.

### What this is NOT

Embeddings do **not** replace the dependency graph. We already compute *exact* structural
relationships (import edges, reference edges, `impact_of`, `reachable_from`) via Jedi. Cosine
similarity is a strictly worse answer to "what depends on X." The two axes are different:

| Axis | Question | Source of truth |
|------|----------|-----------------|
| **Structural / dependency** | "who imports/calls/breaks-if-I-change X" | Jedi graph (exact) — keep |
| **Semantic / purpose** | "what's *like* X", "what does this *do*" | embeddings (this doc) — add |

The semantic layer sits *on top of* the graph and feeds **judgment**, never correctness.

## 2. Current state (what already exists)

- `memory/vector_index.py` — RediSearch **HNSW** index (FLOAT32, COSINE) keyed `"{file}:{fn}"`,
  with a brute-force JSON fallback. **Already capable of what we need**; just under-used.
- `memory/decision_memory.py` — embeds *decision patterns* and recalls them. Only stores decisions,
  not the whole codebase.
- `llm/providers.py` — `LocalEmbeddingProvider` (MiniLM, 384-dim, offline) + `OllamaEmbeddingProvider`.
  **This is the live embedding path.**

### Known issue to fix as part of this work

There are **two embedding modules** and they disagree:
- Live path: `llm/providers.get_embedding_provider()` → MiniLM/Ollama, no OpenAI.
- Legacy: `analysis/embeddings.py` → sentence-transformers **or OpenAI `text-embedding-3-small`**.

`vector_index._current_index_name()` / `_current_dim()` read `analysis.embeddings._PROVIDER` /
`._DIM`, which only update when the *legacy* module embeds — which the live path never does. Result:
index name/dim are derived from a module the live code doesn't use. **Action:** make the index
name/dim a function of the *active* `EmbeddingProvider` (`llm/providers.py`), and either delete
`analysis/embeddings.py` or make it a thin shim over the provider. Pick one embedding source of
truth.

## 3. Scope of this change

In:
- A repo-wide **symbol embedding index** in Redis, built once per run (cached, incremental-friendly).
- Use it in three places: decompose context, consistency recall, and (read-only) a
  `--show-similar` inspection command.
- Replace the `12`-line gate with a composite complexity score.
- Resolve the two-embedding-modules split.

Out (deferred):
- Per-file LLM purpose summaries ("what is this file for") — costs an LLM call per file and can't
  degrade without a key. Revisit once embeddings prove useful.
- New transform engines (`consolidate`, `move`). The semantic index is a *precondition* for them,
  not part of this change.

## 4. Data model in Redis

Reuse the existing `VectorIndex` doc shape, enriched. One document per **symbol** (functions,
methods, classes — not modules):

```
key:        refactorika:vec:{provider}:{dim}:doc:{qualname}
fields:
  embedding   FLOAT32[dim]      # MiniLM(code + signature + 1-line context)
  qualname    TEXT
  file        TEXT
  name        TEXT
  kind        TEXT              # function | method | class
  line        NUMERIC
  content_sha TEXT              # for incremental: skip re-embed if unchanged
```

`content_sha` lets the indexer skip unchanged symbols across runs (the graph is rebuilt per item
today; we don't want to re-embed the whole repo each time).

**What we embed per symbol:** the source text plus its signature and a *small* deterministic
context string assembled from the graph (e.g. `"calls: foo, bar; called_by: baz"`). No LLM. This
keeps embeddings cheap, offline, and reproducible, while folding in a little structural signal.

The dependency graph stays where it is (in-memory `Graph`, serialized to Redis/JSON as today). We
are *not* moving it into the vector store.

## 5. Integration points

### 5.1 Indexer pass — `memory/codebase_index.py` (new)

`build_codebase_index(graph, root, vectors, embed_provider)`:
- For each non-module symbol, compute `content_sha`; skip if a doc with that sha exists.
- Batch-embed the rest (MiniLM batches well); `vectors.upsert(...)` each.
- Pure no-op if `embed_provider.available()` is False — logged, not fatal.

Called once at the start of `orchestrator.run_pipeline` (after the first graph build), guarded so
it degrades silently offline.

### 5.2 Decompose context — `planner_llm._decompose_prompt`

Before prompting, query the index for the target function's nearest neighbors and pass a compact
context block: each neighbor's `qualname` + how it was decomposed before (if recorded). This is the
real payoff — the LLM finally sees the function's semantic neighborhood.

### 5.3 Consistency recall — `decision_memory.recall`

Already does exact-shape → semantic. The change is upstream: with a populated codebase index, the
*candidate set* for "have we decomposed something like this" is the whole repo, not just prior
decisions. Lower the bar from "identical type-stream SHA" to "cosine ≥ threshold on code embedding."

### 5.4 Inspection — `cli.py --show-similar <qualname>`

Read-only: print the k nearest symbols + scores. Cheap to build, makes the index tangible for the
demo, and mirrors the existing `--show-memory` / `--show-graph` pattern.

## 6. Replacing the `12`-line gate (companion change)

`planner_llm._god_functions` currently filters on `size >= _MIN_GOD_LINES` (raw line span). Replace
with a composite score computed from the tree-sitter node we already parse:

```
complexity = w1*branch_count + w2*loop_count + w3*max_nesting_depth
           + w4*distinct_callees + w5*return_points + w6*local_bindings
```

Gate candidates on `complexity >= THRESHOLD` (tuned on `demo_repo` + the eval set). Rationale: a
12-line linear function isn't a god function; a 10-line deeply-nested one might be. The gate's only
job is candidate selection / LLM-cost control, so a better signal directly improves *what* we ask
the LLM to decompose. Keep one tunable constant, documented, not a magic literal.

## 7. Graceful degradation (non-negotiable)

| Condition | Behavior |
|-----------|----------|
| No embedding provider (no `sentence-transformers`, no Ollama) | Indexer no-ops; decompose uses today's single-function prompt; recall falls back to exact-shape. Engine fully correct. |
| No Redis | `VectorIndex` JSON fallback (already implemented). |
| No LLM | No decompose proposals at all (unchanged). |

Nothing here may become a hard dependency of the verified spine.

## 8. Risks / open questions

1. **Whole-symbol embeddings are noisy.** Cosine similarity clusters by vocabulary as much as by
   role. Mitigation: embed code+signature+light-structural-context, not whole files; treat
   similarity as a *ranking hint* for the LLM, never a decision on its own.
2. **MiniLM 384-dim on code.** A general-text model isn't a code model. It's fine for "roughly
   similar function" ranking; don't over-trust absolute scores. (A code-specific embed model is a
   future swap — the provider abstraction already allows it.)
3. **Index freshness during a run.** The graph is rebuilt per item and files change as we edit.
   `content_sha` keying handles staleness, but we should re-index touched symbols after each
   committed edit (cheap, incremental). Decide: re-index eagerly per edit vs. lazily on next query.
4. **Threshold tuning.** The current `0.86` decision threshold was picked for identical shapes;
   broadened semantic recall needs re-tuning on real near-duplicates.

## 9. Phasing

1. **Resolve the two-embedding-modules split** + make index name/dim track the active provider.
2. **Complexity score** replacing `_MIN_GOD_LINES` (isolated, testable on its own).
3. **Indexer pass** (`codebase_index.py`) + `--show-similar` (read-only, low risk).
4. **Wire into decompose context + recall** (the payoff; gated behind embeddings being available).

Each phase is independently shippable and independently degradable.

## 10. As-built notes (implemented 2026-06-21)

All four phases landed. Deviations from the plan above, for the record:

- **Index build lives in the LLM planner, not the orchestrator.** Building it in
  `orchestrator.run_pipeline` would force (no-op) index work on every plain deterministic run.
  Instead `planner_llm.llm_plan` builds it once (only on `--llm`), where the neighbor context is
  actually consumed. `--show-similar` builds on demand in the CLI. Net effect matches the plan
  (build once, degrade cleanly) without taxing the common path.
- **Namespace isolation was required for correctness.** `DecisionMemory.recall` does a `k=1`
  query and assumes the nearest neighbor is a decision. Dumping codebase-symbol vectors into the
  same index would make recall return a code symbol and silently miss real decisions. `VectorIndex`
  gained a `namespace` param; codebase symbols live in `…:codebase` (Redis) / `vectors:codebase`
  (JSON), disjoint from decision vectors. See `test_codebase_vectors_do_not_pollute_decision_recall`.
- **Redis meta via `meta_json`.** The Redis path previously persisted only `file/name/line`; it now
  also stores the full meta dict (qualname, kind, sha) as a JSON field, so rich metadata and the
  incremental `content_sha` skip survive a round-trip. The JSON fallback already stored full meta.
- **OpenAI embeddings became a first-class provider** (`OpenAIEmbeddingProvider`,
  `REFACTORIKA_EMBED_PROVIDER=openai`) rather than being dropped — so the legacy capability is
  preserved through the single provider abstraction.
- **`12`-line gate → union of three shapes:** cyclomatic complexity ≥6 (radon), length ≥30, or
  nesting depth ≥4 (`_is_god_function`, `max_nesting_depth`).

Validated offline with deterministic fake embedders (93 tests pass). Real-embedding behaviour
(MiniLM/Ollama/OpenAI) still needs validation on a machine with the `semantic` extra installed.
