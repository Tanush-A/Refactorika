# Refactorika — System Architecture & Spec (current)

> The authoritative, **as-built** spec for the whole system. Refactorika is an **agent harness delivered as an MCP server** (with a CLI shell too): Claude reasons and proposes edits; Refactorika provides structure-aware analysis, a verification gate stack that proves every mutation safe, and a Redis-backed memory layer with real hybrid search. Everything below is **built and green** (110 tests) unless marked *(parked)*.
>
> Companion docs: `05-redis-iris.md` (the memory layer in depth) · `02-scope.md` (fences) · `v2-worklog.md` (parked hardening items R1–R16) · `v3-worklog.md` (parked: call-site-sweep gate, Sentry).

---

## 1. Architecture at a glance

```
 ┌───────────────────────────────────────────────────────────────────────────┐
 │  CLAUDE  (reasoning agent)   ── reads advisory results · proposes edits      │
 └───────────────┬───────────────────────────────────────────────┬─────────────┘
                 │ MCP (stdio)                                     │ git diffs
                 ▼                                                 ▼
 ┌──────────────────────────────────────────────┐   ┌──────────────────────────┐
 │  MCP SERVER  refactorika/mcp_server.py         │   │  CLI  refactorika/cli.py  │
 │  FastMCP — 12 tools, JSON in/out               │   │  alt shell over the core  │
 │                                                │   │  (audit/plan/check/run)   │
 │  ADVISORY (read-only)        MUTATION (gated)  │   └──────────────────────────┘
 │   analyze_file               apply_and_verify  │
 │   find_duplicates            apply_and_verify_ │   both shells call ONE
 │   find_related                       multi     │   interface-agnostic core ↓
 │   find_dead_code                               │
 │   generate_docs  get_context_map               │
 │   audit_repo  get_plan  confirm_plan  get_log  │
 └───────┬────────────────────────────────┬───────┘
         │ advisory                        │ mutation
         ▼                                 ▼
 ┌────────────────────────────────┐  ┌────────────────────────────────────────┐
 │  ANALYSIS  (read-only)          │  │  GATE STACK  core/apply.py + gates.py    │
 │  core/analyze + analysis/       │  │  atomic: snapshot → … → commit / undo-all│
 │                                 │  │                                          │
 │  parser      tree-sitter front  │  │  1 parse     tree-sitter (no ERROR)      │
 │  analyze     smells + ranking   │  │  2 lint      ruff   (new violations only)│
 │  call_graph  symbol graph       │  │  3 type      pyright(new errors only)    │
 │  dead_code   reachability+conf  │  │  4 behavior  pytest  ◄── proves safe     │
 │  duplicates  fingerprint+hybrid │  │  → git commit  /  restore every file     │
 │  related     impact (similar)   │  │  → EditRecord{checks,status,files,diff}  │
 │  audit       repo report + plan │  └───────────────────┬──────────────────────┘
 │  embeddings  OpenAI / ST        │                      │ refactor history
 │  docs_gen    context extraction │                      │
 └───────────────┬─────────────────┘                      ▼
                 │ read / write                  ┌──────────────────────────────┐
                 ▼                                │  HARNESS  refactorika/harness │
 ┌───────────────────────────────────────────────┴──────────────────────────────┐
 │  REDIS IRIS — memory, via RedisVL     core/storage.py + memory/                │
 │                                                                                │
 │  ① AST cache        ② Hybrid Search Index     ③ Agent memory    ④ Context      │
 │  skip re-parse      per-fn: vector+BM25+tags   cross-session     retriever      │
 │  exact-key hash     FT.HYBRID (RRF) ·          ctx + history     (hybrid +      │
 │  storage.cache_*    OpenAI embeddings          memory/agent_      tag filters)  │
 │                     memory/vector_index        memory            memory/context │
 │                                                                                │
 │  Redis 8.4+ Query Engine (local Docker redis:8 / Cloud) for FT.HYBRID;          │
 │  brute-force vector when absent. ── offline fallback: .refactorika/*.json ──    │
 └────────────────────────────────────────────────────────────────────────────────┘

 External: OpenAI (embeddings) · git (commits) · ruff/pyright/pytest (gates) · Docker (Redis)
```

**Read it as four bands.** Claude (outside the harness) drives the **MCP server** (or the CLI). The shell routes **advisory** tools through the read-only **analysis** layer and **mutations** through the atomic **gate stack**. Both sit on **Redis Iris** — four memory components accessed via RedisVL, with a mandatory local-file/brute-force fallback so it always runs offline. The **harness** is a synthetic test driver over the gate stack.

---

## 2. The tool surface (12 MCP tools, all built)

Everything is **advisory** (read-only — finds & explains) or a **verified mutation** (the atomic gated entrypoint). Advisory output feeds Claude's reasoning; Claude proposes concrete edits; the mutation entrypoint proves them safe and commits.

### Advisory (read-only)
| Tool | Does |
|---|---|
| `analyze_file(path)` | Ranked structural smells for one file (file size, import order/dupes, function length, nesting). |
| `find_duplicates(path, threshold=0.55)` | Duplicate functions: tier-1 structural AST fingerprint **+** tier-2 semantic via **FT.HYBRID**. Ranked pairs + consolidation target. |
| `find_related(path, symbol, k)` | **Impact check:** functions elsewhere that are semantically similar (hybrid) **+** modules that depend on this file (call graph). |
| `find_dead_code(path)` | Unreachable symbols via call-graph reachability, ranked high/medium/low confidence. |
| `generate_docs(path)` | Emit/update `.refactorika/context/<module>.md`, persist `ModuleContext` to agent memory; incremental on re-run. |
| `get_context_map(path)` | Cross-session context for a module + **related modules via the context retriever** (hybrid). |
| `audit_repo(path)` | Repo-wide ranked opportunity report (which files, which smells, headline finding). |
| `get_plan(path)` | Dependency-ordered refactor plan (fewest-dependents-first); persisted. |
| `confirm_plan(decision, order)` | Human checkpoint — approve / reject / reorder the persisted plan. Never changes code. |
| `get_log()` | The append-only `EditRecord` log (powers the dashboard). |

### Verified mutation (atomic, gated)
| Tool | Does |
|---|---|
| `apply_and_verify(path, new_content, refactor_kind)` | Apply Claude's contents through the gate stack; commit on green / roll back on fail; append an `EditRecord`. |
| `apply_and_verify_multi(edits, refactor_kind)` | Same, atomically across **multiple files** (cross-file duplicate merges). |

`refactor_kind` covers every organization/complexity edit **and** `consolidate_duplicate` / `remove_dead_code` — removals are ordinary mutations that must pass `pytest`. That is how "find dead code" becomes "**safely remove** it, proven by your tests."

---

## 3. Layers, in detail

### 3.1 Shells — MCP (primary) + CLI
- `mcp_server.py` — `FastMCP("refactorika")`, one thin `@mcp.tool()` per capability over the core. Run: `python -m refactorika.mcp_server`.
- `cli.py` — an alternate shell (`refactorika audit/plan/check/run`) for git-diff / CI use without a live agent; proposes edits itself. Same core, same gates.
- `dashboard.py` — renders the audit / plan / edit-log story for the demo (`render_audit`, `render_plan`, `render_campaign`).

### 3.2 Analysis layer (read-only) — `core/analyze.py` + `analysis/`
`parser` (shared tree-sitter front end) · `analyze` (smells + ranking) · `call_graph` (symbol graph, `dependents_of`) · `dead_code` (reachability + confidence) · `duplicates` (structural fingerprint + hybrid semantic) · `related` (impact: hybrid neighbours + dependents) · `audit` (`audit_repo` + `build_plan`) · `embeddings` (OpenAI primary, sentence-transformers keyless fallback; `provider_dim()`) · `docs_gen` (context extraction, `generate_docs`/`get_context_map`).

### 3.3 Verification gate stack — `core/apply.py` + `core/gates.py`
Atomic single entrypoint (`apply_and_verify` delegates to `apply_and_verify_multi`): snapshot all files → **parse-gate all before writing** → write → **lint** (ruff, *new* violations vs. baseline) → **type** (pyright, *new* errors vs. baseline — not absolute) → **behavior** (pytest once) → all green: one `git commit`; any fail/exception: **restore every file**. Emits one `EditRecord{file, files, refactor_kind, checks{parse,lint,typecheck,tests}, retries, status, failure_reason, diff}`, `status ∈ {committed, rolled-back, skipped-needs-human}`. Skipped gates recorded as `null`, never silent-passed.

### 3.4 The v3 campaign — `audit_repo → get_plan → confirm_plan`
Widens from one file to a **whole-repo campaign with a human in the loop**: `audit_repo` aggregates per-file analysis into a ranked report; `get_plan` orders deviating files **fewest-dependents-first** (low blast radius first, via the call graph) and persists a `Plan`; `confirm_plan` is the single human gate (approve/reject/reorder). Claude then drives `apply_and_verify` task-by-task in plan order. Additive — the gate stack is untouched.

### 3.5 Redis Iris — memory via RedisVL (full detail in `05-redis-iris.md`)
Four components: **AST cache** (exact-key, skip re-parse) · **Hybrid Search Index** (per-function `vector + body(BM25) + tags`, queried with `FT.HYBRID`, RRF-fused, OpenAI embeddings) · **Agent memory** (cross-session module context + refactor history) · **Context retriever** (hybrid retrieval + tag/num filters). All via RedisVL `SearchIndex`/`HybridQuery`; degrade to brute-force vector + JSON files offline.

---

## 4. Redis setup (as-run)

- **Recommended (local, full hybrid):** Docker `redis:8` (8.8, has `FT.HYBRID`) — `docker run -d --name refactorika-redis --restart=always -p 6380:6379 redis:8`, then `REDIS_URL=redis://localhost:6380`. Auto-restarts whenever Docker Desktop runs; **nothing to launch manually**.
- **Also works:** Redis Cloud / Redis Stack (any Redis 8.4+ with the Query Engine).
- **Degraded but functional:** bare `redis-server` (no Query Engine) → brute-force vector over real embeddings (no BM25 fusion).
- **Offline:** no Redis → local `.refactorika/state.json` + brute-force. `REDIS_URL` in `.env` drives it all; the harness auto-connects on startup and falls back silently.

Embeddings need `OPENAI_API_KEY` (or the keyless `sentence-transformers` fallback); both live behind `pip install '.[semantic]'` (`openai` + `redisvl` + `sentence-transformers`).

---

## 5. Status

- **Built & green (110 tests):** all 12 tools; the gate stack (with baseline-aware lint+type); the v3 campaign; `find_related`; the full Redis Iris memory layer with **live `FT.HYBRID`** (semantic dedup + vector context retrieval verified working end-to-end). The verified-refactor "trust spine" demo runs offline and on Redis.
- **Parked (real, non-demo-blocking):** review findings **R1–R16** (`v2-worklog.md`) — CLI `imports` transform code-loss, `apply.py` atomicity edge cases, gate exit-code nuances, G1 absolute-path cache; deferred `[decide]/[tune]` (F1–F4); and v3 stretch (`v3-worklog.md`) — call-site-sweep gate, Sentry.

## 6. Module map

```
refactorika/
  mcp_server.py · cli.py · dashboard.py · harness.py · docs_gen.py
  core/      schema · analyze · apply · gates · storage
  analysis/  parser · analyze* · call_graph · dead_code · duplicates · related · audit · embeddings
  memory/    vector_index (RedisVL hybrid) · agent_memory · context
  transforms/ imports · dead          (CLI edit generators)
demo_repo/   curated target + tests        scripts/demo.py  campaign walkthrough
tests/       ~110 unit + full-system harness; tests/test_hybrid_live.py (live, skip-guarded)
```
(* `core/analyze.py` holds the original smell analyzer; `analysis/` holds everything else.)
</content>
