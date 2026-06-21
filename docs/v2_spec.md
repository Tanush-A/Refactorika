# Refactorika — Build Spec (v2, SUPERSEDED)

> ⚠️ **Superseded by [`v3_spec.md`](v3_spec.md).** This document describes the earlier
> *MCP-harness* model, where an external Claude proposed whole-file `new_content` and a gate
> stack verified it. The product has since been rebuilt into a **graph-driven, autonomous
> refactoring engine** (Jedi reference resolution + deterministic transform engines +
> leaf-to-root pipeline + standalone CLI). Read `v3_spec.md` for the as-built system; this file
> is kept for history only. The gate-stack and Redis-fallback ideas below carried forward; the
> "Claude proposes whole files" model did not.

> The single buildable spec for the full product. Grounded in the shipped code (`refactorika/core/`) and docs 01–05. Where a number/threshold is a guess it's marked **[tune]**; where a design choice is open it's marked **[decide]**. Eval/benchmark docs (11–12) are intentionally out of scope here.
>
> **Resolved up front:** semantic embeddings ship as an **optional extra** (`refactorika[semantic]`); the multi-file `EditRecord` gains a **`files: list[str]`** field. Redis Iris is the **full four-component** memory layer (AST cache · vector index · cross-session agent memory · context retriever).

## 0. Status at a glance

| Capability | Tool(s) | Status | New modules |
|---|---|---|---|
| Structural analysis | `analyze_file` | **shipped** | — |
| Verified mutation (atomic gate stack) | `apply_and_verify` | **shipped** | — |
| Edit log / dashboard | `get_log` | **shipped** | — |
| Duplicate detection | `find_duplicates` | build | `analysis/duplicates.py`, `analysis/embeddings.py`, `memory/vector_index.py` |
| Dead-code detection | `find_dead_code` | build | `analysis/dead_code.py`, `analysis/call_graph.py` |
| Verified merge / removal | `apply_and_verify` (new kinds) + `apply_and_verify_multi` | build | extend `core/apply.py`, `core/schema.py` |
| Living docs | `generate_docs` | build | `docs_gen.py`, `memory/agent_memory.py` |
| Cross-session context | `get_context_map` | build | `memory/agent_memory.py`, `memory/context.py` |
| Redis Iris (4 components) | (infra) | cache shipped; vector/memory/retriever build | extend `core/storage.py`, `memory/*` |

Everything is one of two classes: **advisory** (read-only — finds and explains) or **verified mutation** (the single atomic `apply_and_verify` entrypoint). Advisory output feeds Claude's reasoning; Claude proposes concrete edits; the mutation entrypoint proves them safe, commits, and writes the outcome to agent memory.

---

## 0.1 Architecture at a glance

Four layers, top to bottom. Claude sits on top and talks only to the MCP server; everything below is the harness.

```
 ┌───────────────────────────────────────────────────────────────┐
 │  CLAUDE  — the reasoning agent (outside the harness)          │
 │  decides WHAT to change · writes the new code                 │
 └───────────────────────────────────────────────────────────────┘
        │ calls a tool                      ▲ gets a result back
        ▼                                   │
 ┌───────────────────────────────────────────────────────────────┐
 │  MCP SERVER   (mcp_server.py)  — the only thing Claude talks to│
 │                                                               │
 │   ADVISORY  (look, don't touch)                               │
 │     analyze_file · find_duplicates · find_dead_code           │
 │     generate_docs · get_context_map · get_log                 │
 │                                                               │
 │   MUTATION  (change code — but only if it passes)             │
 │     apply_and_verify · apply_and_verify_multi                 │
 └───────────────────────────────────────────────────────────────┘
        │                                   │
   advisory tools                      mutation tools
   go here                             go here
        ▼                                   ▼
 ┌─────────────────────────┐   ┌─────────────────────────────────┐
 │  ANALYSIS  (read-only)  │   │  GATE STACK  (atomic: all-or-    │
 │  reads code, finds      │   │  nothing — never leaves a mess)  │
 │  problems:              │   │                                 │
 │                         │   │   1. parse   (tree-sitter)      │
 │   • smells / ranking    │   │   2. lint    (ruff)             │
 │   • duplicates          │   │   3. types   (pyright)          │
 │   • dead code           │   │   4. tests   (pytest) ← the     │
 │   • context for docs    │   │                proof it's safe  │
 │                         │   │   ──────────────────────────    │
 │                         │   │   all pass → git commit         │
 │                         │   │   any fail → undo + say why     │
 └─────────────────────────┘   └─────────────────────────────────┘
        │                                   │
        └─────────────────┬─────────────────┘
                          ▼
 ┌───────────────────────────────────────────────────────────────┐
 │  REDIS IRIS  — memory, shared by both sides                   │
 │                                                               │
 │   1. AST cache       don't re-analyze unchanged code          │
 │   2. Vector index    find look-alike functions                │
 │   3. Agent memory    remember context across sessions         │
 │   4. Context fetch   pull the relevant past notes             │
 │                                                               │
 │   no Redis running?  →  falls back to .refactorika/ files.    │
 │   always works offline.                                       │
 └───────────────────────────────────────────────────────────────┘
```

**The loop, in plain steps:**

1. Claude calls an **advisory** tool → the **analysis** layer reads the code and hands back a ranked list of problems (smells, duplicate pairs, dead symbols, context).
2. Claude reads that and **proposes** a concrete edit — it writes the new file contents.
3. Claude calls a **mutation** tool → the **gate stack** runs parse → lint → types → tests.
4. **All green → commit.** **Any red → undo the change and return the reason.**
5. On a failure, Claude reads the reason and **re-proposes** — back to step 2.

**Redis Iris** sits under both sides as shared memory (so re-runs are faster and context survives across sessions), but it's never required — if Redis isn't running, everything degrades to local files and still works offline.

---

## 1. The foundation new code plugs into (shipped — do not rebreak)

- **`core/schema.py`** — frozen contracts: `Opportunity`, `AnalysisResult`, `GateChecks`, `EditRecord`, `REFACTOR_KINDS`, `Status`. All have `to_dict()`. New result types extend this file in the same style.
- **`core/analyze.py`** — `analyze_file(path, storage) -> AnalysisResult`. tree-sitter walk; thresholds `MAX_FILE_LINES=150`, `MAX_FUNC_LINES=30`, `MAX_NESTING=3`. Caches on a sha1 AST/content signature via `storage`.
- **`core/apply.py`** — `apply_and_verify(path, new_content, refactor_kind, storage) -> EditRecord`. Atomic: snapshot → parse → ruff → pyright → pytest → `git commit` on green / restore on fail. Working tree never left dirty.
- **`core/gates.py`** — `parse_gate`, `lint_gate`, `typecheck_gate`, `test_gate`, `ruff_baseline`. Each returns `(True|False|None, detail)`; `None` = skipped-and-recorded.
- **`core/storage.py`** — `Storage`: Redis primary (`REDIS_URL`, else `redis://localhost:6379/0`), local-JSON fallback (`.refactorika/state.json`), `.env` auto-load, 0.5s connect timeout. Methods: `append_log`, `get_log`, `count_attempts`, `cache_get`, `cache_set`. Keys: `refactorika:log` (list), `refactorika:cache` (hash). The `memory/` package generalizes this same connect-and-fallback pattern to vectors and agent memory.
- **`mcp_server.py`** — `FastMCP("refactorika")`; one thin `@mcp.tool()` per capability wrapping a core call, returning JSON-serializable dicts. New tools register here.

**Shared helper to extract once** (used by every new module): a function-node walker. `analyze.py` already has `_funcs(node)`, `_func_name(node)`. Promote these into `analysis/parser.py` (new) so `duplicates`, `dead_code`, and `docs_gen` share one tree-sitter front end instead of re-implementing walks.

---

## 2. New MCP tool surface (freeze these signatures)

```python
# all return JSON-serializable dicts; all read-only except apply_and_verify*

find_duplicates(path: str, threshold: float = 0.83) -> dict
find_dead_code(path: str) -> dict
generate_docs(path: str) -> dict
get_context_map(path: str) -> dict
# verified mutation:
apply_and_verify(path: str, new_content: str, refactor_kind: str) -> dict          # single-file (shipped)
apply_and_verify_multi(edits: dict[str, str], refactor_kind: str) -> dict           # NEW multi-file atomic
```

`path` accepts a file or a directory (recurse over `*.py`, skipping `.venv`, `__pycache__`, `tests` unless asked). `threshold` is the cosine cutoff for semantic pairs.

### 2.1 `find_duplicates` return

```json
{
  "path": "demo_repo/",
  "pairs": [
    {
      "a": {"file": "svc/format.py", "name": "fmt_date", "line": 12},
      "b": {"file": "ui/cards.py",   "name": "format_day", "line": 88},
      "similarity": 0.94,
      "match_type": "semantic",          // "structural" | "semantic"
      "consolidation_target": {"file": "svc/format.py", "name": "fmt_date"},
      "reason": "same logic; target has 4 call sites vs 1",
      "rank": 94
    }
  ]
}
```

### 2.2 `find_dead_code` return

```json
{
  "path": "demo_repo/",
  "entry_points": ["orders.compute_total", "__main__", "test_*"],
  "dead_symbols": [
    {
      "kind": "function",               // function | class | assignment
      "name": "_legacy_discount",
      "file": "svc/pricing.py",
      "line": 140,
      "confidence": "high",             // high | medium | low
      "reason": "private symbol, zero references from any entry point",
      "rank": 90
    }
  ]
}
```

### 2.3 `generate_docs` return (+ side effects)

Writes `.refactorika/context/<module>.md`, **persists the structured context to Redis Iris agent memory** (so the next session retrieves it), and returns the skeleton so Claude can enrich prose in-conversation.

```json
{
  "path": "svc/pricing.py",
  "context_file": ".refactorika/context/svc.pricing.md",
  "persisted_to": "agent_memory",         // "agent_memory" | "json_fallback"
  "incremental": true,                      // true if a prior context entry was diffed
  "module": {
    "purpose_hint": "Pricing + discount calculation (inferred from names/docstrings)",
    "exports": [{"name": "compute_total", "kind": "function", "signature": "(items, tier, coupon) -> float"}],
    "dependents": ["api/checkout.py", "jobs/retry.py"],
    "flagged": ["math.floor on line 43 — non-obvious rounding to 2 dp"],
    "changed_since_last": ["compute_total signature gained `coupon`"]
  }
}
```

### 2.4 `get_context_map` return

Pulls the accumulated cross-session context for a module/dir from agent memory (or the JSON fallback), without re-deriving structure.

```json
{
  "path": "svc/pricing.py",
  "source": "agent_memory",               // "agent_memory" | "json_fallback" | "derived"
  "context": { "purpose": "…", "exports": [...], "dependents": [...], "decisions": [...] },
  "last_updated_run": "…",
  "related": [{"module": "svc.billing", "score": 0.88}]   // via context retriever (vector)
}
```

All new schemas live in `core/schema.py` as dataclasses with `to_dict()`: `SymbolRef`, `DuplicatePair`, `DeadSymbol`, `ExportRef`, `ModuleContext`.

---

## 3. Component spec — Duplicate detection

**Two tiers with non-overlapping jobs** (per `05-redis-iris.md`).

### 3.1 Tier 1 — structural fingerprint (precise, cheap)
1. For each function node, walk its subtree and emit a **canonical token stream of node *types*** (e.g. `function_definition, parameters, block, if_statement, comparison_operator, return_statement, …`), **dropping identifier text and literal values** (replace with `ID` / `LIT` placeholders).
2. `sha1` the stream → the structural fingerprint.
3. Store/look up in the existing AST cache (`storage.cache_*`, Redis hash `refactorika:cache`).
4. **Equal fingerprints = structural duplicates** (similarity `1.0`, `match_type: "structural"`). Catches copy-paste-then-rename clones with zero false positives.

*Near-exact* **[decide]**: optionally compute a token-sequence ratio (`difflib.SequenceMatcher` over the type stream) and report pairs ≥ `0.95` as structural too. Start with exact-hash only; add ratio if recall is weak.

### 3.2 Tier 2 — semantic embeddings (catches different-shape duplicates)
1. Build an embedding **input string** per function = signature + body source + docstring (the *real* text, not the denatured shape).
2. Embed via `analysis/embeddings.py` (see §6) — **requires the `[semantic]` extra**; if it's not installed, `find_duplicates` runs tier-1 only and says so in the response (`"semantic": "unavailable — install refactorika[semantic]"`).
3. Upsert into the vector index keyed `{file}:{function_name}` (see §7).
4. For each function, query top-k neighbors by cosine; emit pairs with `similarity ≥ threshold` (default `0.83` **[tune]**) and `match_type: "semantic"`.
5. **Dedupe** against tier-1 pairs (don't report a pair both ways or in both tiers).

### 3.3 Ranking & consolidation target
- `rank = round(similarity * 100)`; sort desc.
- **Consolidation target** = the function with more call sites (from the call graph in §4) or, on a tie, the one in the more central/imported module. Surfaced in `reason`. Never auto-merged — Claude proposes the merge as a `consolidate_duplicate` mutation (§5).

---

## 4. Component spec — Dead-code detection

### 4.1 Call graph (`analysis/call_graph.py`)
- **Nodes:** module-level symbols — `function_definition`, `class_definition`, and module-level assignments. Key by `module.qualname` (e.g. `svc.pricing.compute_total`). Methods inside classes collapse under their class node for v1 **[decide]** (method-level reachability is a later refinement).
- **Edges:** name references — `call` expressions, attribute access, and `import` / `import_from` statements. Resolve a referenced name to a node by (a) same-module symbol table, then (b) imported-name map. Unresolved names are ignored (they point outside the analyzed set).
- Build over the **whole `path`** (directory), not one file, so cross-file references count.

### 4.2 Entry points (reachability anchors)
A symbol is an entry point if **any** of:
- listed in `__all__`, or its name has no leading `_` (public API — conservatively reachable);
- defined/called inside an `if __name__ == "__main__":` block;
- a test callee — name referenced from any `test_*` function or `tests/` file;
- decorated by a registration decorator (`@app.route`, `@click.command`, `@pytest.fixture`, …) **[tune list]**.

### 4.3 Reachability + confidence
- BFS/DFS from all entry points over the edges; any node **not** reached is a dead-code candidate.
- **Confidence:**
  - `high` — **private** (`_name`), zero references from anywhere.
  - `medium` — public but unreferenced inside the analyzed set (may be external API or dynamic).
  - `low` — name also appears inside a string literal anywhere (possible `getattr`/reflection/dynamic dispatch) → flag, don't trust.
- `rank` = `{high:90, medium:60, low:30}` + small tie-breaker.
- **Never auto-delete.** Removal happens only as a `remove_dead_code` mutation Claude proposes, proven by `pytest` (§5).

**Known limits (document, don't silently ignore):** dynamic dispatch, `getattr`, plugin registries, and entry points reached only via external packages can produce false positives — that's exactly why confidence + the `pytest` gate exist, and why public symbols cap at `medium`.

---

## 5. Verified mutation for merge/removal (extend the gate stack)

Duplicate consolidation and dead-code removal are **ordinary mutations** — they must pass the same `parse → ruff → pyright → pytest` gates. Two additions:

### 5.1 New `refactor_kind` values
Add to `REFACTOR_KINDS` in `core/schema.py`: `"consolidate_duplicate"`, `"remove_dead_code"`. No other code path changes — the gate stack is kind-agnostic; `pytest` is what proves a deletion safe.

### 5.2 Multi-file atomic apply (**required** for consolidation) + `EditRecord.files`
`apply_and_verify` today takes one `(path, new_content)`. A duplicate merge usually touches ≥2 files (delete the dup in B, import the canonical from A) — applying them as two sequential single-file edits would break tests *between* steps and roll back spuriously.

**Add `apply_and_verify_multi(edits: dict[path -> new_content], refactor_kind, storage)`:**
1. Snapshot every target file.
2. Parse-gate each `new_content` (before touching disk).
3. Write all; capture one combined ruff baseline (union of touched files).
4. Run lint/type on each touched file, `pytest` **once** over the repo.
5. All green → `git add` all + one commit. Any fail/exception → restore **all** snapshots.
6. Emit one `EditRecord`.

**Schema change (resolved):** add `files: list[str]` to `EditRecord`; `file` stays as the first/primary path for back-compat. `to_dict()` emits both. Single-file `apply_and_verify` sets `files=[path]` and delegates to `_multi` with a one-entry dict (one code path, two entrypoints).

---

## 6. Embeddings (`analysis/embeddings.py`) — optional `[semantic]` extra

```python
def embed(texts: list[str]) -> list[list[float]]: ...
def embed_one(text: str) -> list[float]: ...
def available() -> bool: ...   # False if neither provider importable
```

- **Primary (built):** OpenAI `text-embedding-3-small` (1536-dim) — used whenever `OPENAI_API_KEY` is set, unless `REFACTORIKA_EMBED=local` forces the keyless path. `provider_dim()` returns the intended `(provider, dim)` without a network call so the index can name itself first.
- **Keyless fallback:** `sentence-transformers` `all-MiniLM-L6-v2` (384-dim), offline. Lazy-import inside the function so importing the module never pulls torch.
- **Packaging:** the deps (`openai`, `sentence-transformers`/torch, `redisvl`) live behind the **`refactorika[semantic]` optional extra**. Without it, `available()` is `False`, duplicate detection runs structural-only, and tools degrade gracefully (never crash on a missing import).
- **Dimension** is provider-dependent — store it alongside vectors so a provider switch invalidates cleanly (namespace the hybrid index by `{provider}:{dim}`).
- Search runs through **RedisVL hybrid queries** (see §7.2), not raw cosine; brute-force `numpy` cosine is only the offline fallback. Batch in `embed`.

---

## 7. Redis Iris — the four components (`core/storage.py` + `memory/`)

Per `05-redis-iris.md`: **four cooperating components**, Redis primary, local fallback mandatory. The `memory/` package wraps Redis with the same connect-and-fallback pattern as `Storage`.

### 7.1 AST-keyed cache (shipped — `core/storage.py`)
Redis hash `refactorika:cache`, keyed on normalized AST signature. **Exact key, never fuzzy.** Used by `analyze_file`, tier-1 fingerprints, ruff baselines.

### 7.2 Hybrid search index (`memory/vector_index.py`) — **BUILT**, via RedisVL
- **Backend:** a RedisVL `SearchIndex` (Redis 8.4+ Query Engine — Redis Cloud / Redis Stack). Index `refactorika:vec:{provider}:{dim}` (provider/dim from `embeddings.provider_dim()`, computed *before* the first embed). Each doc = `{file}:{fn}` with fields: `embedding` (vector, HNSW, cosine, `dims`), `body` (text, BM25STD), `line` (numeric), and `file`/`module`/`name`/`fingerprint` (tags).
- **API (as built):** `upsert(key, vector, meta=None, *, text="")` — `meta` stays 3rd-positional for back-compat, `text` keyword-only · `query(vector, k=5, threshold=0.0)` vector-only (unchanged) · `query_hybrid(vector, text, k=5, filters=None) -> [Neighbor{key,score,meta}]` (`HybridQuery`, RRF, BM25STD) · `module_filter(m) -> FilterExpression|None` · `drop()`.
- **Similarity reporting:** RRF scores aren't cosine, so `find_duplicates` recomputes true cosine between the two known function vectors for `DuplicatePair.similarity` and the `threshold` gate (stable across hybrid/fallback). `query_hybrid` itself takes no threshold.
- **Why hybrid:** pure cosine is weak on code (misses exact identifiers, false-positives on unrelated helpers). `FT.HYBRID` fuses BM25 (identifiers/body) with vector (meaning) — Redis reports 3–3.5× recall, +11–15% accuracy vs. single-mode. RRF default; linear+alpha only if one signal should dominate.
- **Fallback:** when `storage._redis is None` or redisvl is absent (`_use_redisvl=False`), `query_hybrid` **delegates to vector-only `query()`** and entries persist as `{key:{vector,text,meta}}` in `.refactorika/state.json` with brute-force numpy cosine — same correctness floor, BM25 dropped.
- **Deps:** `redisvl>=0.13` (+ `redis-py` ≥ 7.1, satisfied) in the `[semantic]` extra.

### 7.3 Agent memory (`memory/agent_memory.py`) — build, **cross-session**
- **Stores:** per-module context (`ModuleContext` from `generate_docs`), architectural decisions, and refactor history (the `EditRecord` stream — generalizes the existing `refactorika:log`).
- **Keys:** Redis hash `refactorika:memory:context` (`module_path -> ModuleContext json`), reuse `refactorika:log` for history.
- **API:** `put_context(module, ctx)` · `get_context(module) -> ModuleContext|None` · `history(file) -> [EditRecord]`.
- **Cross-session:** persists between runs, so the second run on a repo retrieves prior context and works incrementally. **Fallback:** `context` + `log` maps in `.refactorika/state.json` and `.refactorika/context/<module>.md`.

### 7.4 Context retriever (`memory/context.py`) — build
- **Structured:** call sites of a symbol, import conventions, module dependents (from the call graph + agent memory).
- **Vector:** top-k relevant prior context entries for a module via the vector index (embed the `ModuleContext` summary too).
- **API:** `relevant(module, k=3) -> [{module, score}]` · `conventions(path) -> dict` · `dependents(module) -> [str]`.
- Powers incremental `generate_docs` (retrieve last → diff → update only what changed) and grounds `apply_and_verify` proposals in existing conventions. **Fallback:** structured lookups over the AST; vector lookups via the brute-force scan.

---

## 8. `generate_docs` + `get_context_map` (`docs_gen.py`)

- **Extract (deterministic, tree-sitter):** purpose hint (first docstring / dominant noun in names), exports + signatures (top-level non-`_` defs, `__all__`), dependents (from the call graph), "flagged" lines (bare `except`, `getattr`, magic constants, `# noqa`, in-function imports).
- **Incremental:** call the context retriever for the prior `ModuleContext`; diff and report `changed_since_last`. First run = full; later runs = delta.
- **Emit + persist:** write a templated `.refactorika/context/<module>.md` (`svc/pricing.py` → `svc.pricing.md`: Purpose · Exports · Dependents · Decisions) **and** `agent_memory.put_context(module, ctx)` so it survives the session.
- **Prose** **[decide §14]:** the `Purpose`/`Decisions` narrative is best written by Claude from the extracted skeleton — `generate_docs` writes extracted facts + `<!-- claude: fill -->` placeholders and returns the skeleton; Claude replaces them in-conversation. (Default: skeleton; keeps the tool deterministic/offline.)
- **`get_context_map(path)`** is the read side: return the persisted `ModuleContext` (+ retriever `related` modules) without re-deriving; falls back to deriving on a cold cache.

---

## 9. File layout (target)

```
refactorika/
├── mcp_server.py            # + register find_duplicates/find_dead_code/generate_docs/get_context_map   (modify)
├── dashboard.py             # (exists)
├── core/
│   ├── schema.py            # + SymbolRef, DuplicatePair, DeadSymbol, ModuleContext, files field, new kinds  (modify)
│   ├── analyze.py           # (exists; move shared walkers to analysis/parser.py)
│   ├── apply.py             # + apply_and_verify_multi                                    (modify)
│   ├── gates.py             # (exists, unchanged)
│   └── storage.py           # + vectors fallback map; back agent-memory fallback          (modify)
├── analysis/
│   ├── parser.py            # shared tree-sitter front end (funcs, names, imports)         (new)
│   ├── duplicates.py        # tier-1 fingerprint + tier-2 semantic pairing                 (new)
│   ├── dead_code.py         # entry points + reachability + confidence                     (new)
│   ├── call_graph.py        # directed symbol graph                                        (new)
│   └── embeddings.py        # local/OpenAI embedding ([semantic] extra)                    (new)
├── memory/
│   ├── vector_index.py      # RediSearch vector index + JSON brute-force fallback          (new)
│   ├── agent_memory.py      # cross-session context + refactor history                     (new)
│   └── context.py           # Context Retriever (structured + vector)                       (new)
└── docs_gen.py              # generate_docs + get_context_map                              (new)
```

`src/refactorika/` (the abandoned stub) is **deleted** as part of this work; `pyproject.toml` repointed to `refactorika/` with a `[project.scripts] refactorika = "refactorika.mcp_server:main"` entry point.

---

## 10. Dependencies (`pyproject.toml`)

| Package | Where | Why |
|---|---|---|
| `numpy` | core | brute-force cosine fallback, vector math |
| `redis` | core (exists) | Redis client (cache + agent memory) |
| `redisvl` | **`[semantic]` extra** | RedisVL — index schema + `HybridQuery` (FT.HYBRID); needs redis-py ≥ 7.1 |
| `openai` | `[semantic]` extra | primary embedding provider (`text-embedding-3-small`) |
| `sentence-transformers` | `[semantic]` extra | keyless/offline embedding fallback (pulls torch ~2GB) — lazy-import |

Clean install stays light (structural duplicate detection + everything else works); `pip install 'refactorika[semantic]'` adds the embedding providers + RedisVL hybrid search. Tools degrade gracefully when the extra is absent (structural-only) or when no Query Engine is reachable (brute-force vector fallback).

---

## 11. Demo additions (curated `demo_repo/`)

- **Semantic duplicate:** two functions computing the same thing with different names/structure (`line_total` in `orders.py` and a `_compute_line` variant in a new `billing.py`) → `find_duplicates` returns the pair → Claude proposes `consolidate_duplicate` → `apply_and_verify_multi` proves green → commit. (Redis Insight shows the vector index populate.)
- **Dead code:** a private `_legacy_*` function nothing calls → `find_dead_code` flags `high` → `remove_dead_code` → `pytest` proves safe → commit. Plant a second "dead-looking" function actually reached via tests, to show a `medium`/`low` correctly *not* removed.
- **Docs + memory:** `generate_docs("orders.py")` emits `.refactorika/context/orders.md` and writes agent memory; re-run to show `get_context_map` returning it and `incremental: true` after a refactor (visible in Redis Insight).
- Keep the existing planted behavior-break (tax 8%→5%) demo intact — it's the trust spine.

---

## 12. Testing plan (`tests/`)

Mirror the fast, dependency-light style of `test_core.py` (no network; tmp_path; inject a deterministic stub embedder via the provider seam so tests stay offline):
- `test_duplicates.py` — identical structural fingerprint for rename-only clones; distinct for different logic; semantic tier surfaces a different-shape pair above threshold (stub embedder); graceful tier-1-only when `embeddings.available()` is False.
- `test_dead_code.py` / `test_call_graph.py` — private-unreferenced=`high`, public-unreferenced=`medium`, name-in-string=`low`, entry-point-reached absent; cross-file edge resolution; `__all__`/`test_*` anchoring.
- `test_apply_multi.py` — two-file atomic apply commits together; a failure in one restores **both**; `EditRecord.files` correct.
- `test_agent_memory.py` — JSON fallback put/get context round-trips; history filter; cross-"session" persistence (two `Storage`/memory instances, same json_path).
- `test_docs_gen.py` — exports/dependents extraction; context file at the right dotted path; `get_context_map` returns persisted ctx; `incremental` flips on second run.
- `test_vector_index.py` — JSON fallback upsert+query returns nearest by cosine (deterministic vectors).
Keep real-embedder and RediSearch-backed paths behind marks/skips so `pytest -q` stays green offline.

---

## 13. Build order (from `02-scope.md`)

1. **Verified-refactor loop** — *shipped*, keep green.
2. **Duplicate detection** — `parser.py` → `duplicates.py` tier-1 (structural, no embeddings, demoable) → `embeddings.py` + `vector_index.py` for tier-2 → `find_duplicates` → `consolidate_duplicate` + `apply_and_verify_multi`.
3. **Dead-code + verified removal** — `call_graph.py` → `dead_code.py` → `find_dead_code` → `remove_dead_code`.
4. **Cross-session memory + living docs** — `agent_memory.py` + `context.py` → `docs_gen.py` → `generate_docs` + `get_context_map`.

Each step is independently demoable; tier-1 structural duplicates ship value before the embedding stack exists.

---

## 14. Open decisions (collected)

1. ~~`sentence-transformers` core vs extra~~ — **resolved: optional `[semantic]` extra.** (§6, §10)
2. ~~Multi-file `EditRecord` shape~~ — **resolved: add `files: list[str]`, keep `file` as primary.** (§5.2)
3. **`generate_docs` prose:** skeleton+placeholders for Claude vs tool calls a model? (Recommend skeleton.) — §8
4. **Method-level reachability** in the call graph (v1 collapses methods under class). — §4.1
5. **Near-exact structural tier** (exact-hash only vs add `SequenceMatcher` ratio). — §3.1
6. **Vector backend:** RediSearch (best demo) vs Redis-hash+numpy vs JSON-only. — §7.2
7. Default thresholds (`0.83` semantic, confidence cutoffs) need **[tune]** on the curated repo.

## 15. Consistency cleanup this spec assumes (not yet done)

- **Delete `src/refactorika/`** stub tree; repoint `pyproject.toml` (`packages = ["refactorika"]`, `[tool.pyright] include = ["refactorika"]`, `[tool.ruff] src = ["refactorika"]`) and add the `[project.scripts]` entry point. Until then `pip install -e .` packages an empty tree and the `refactorika` command doesn't exist (use `python -m refactorika.mcp_server`).
- Add the `[project.optional-dependencies] semantic = [...]` extra (§10) alongside the existing `dev` extra.
</content>
