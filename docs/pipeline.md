# Refactorika pipeline & code-reachability map (as built)

How a run actually moves through the codebase, what every module is for, and **which code is
reachable vs. dead** after the `main` ⇄ `v3-refactoring-engine` merge fused two architectures.
Source of truth is the code under `refactorika/`; if this disagrees with `docs/v3_spec.md`,
trust the code. The reachability claims here are reproducible:

```bash
.venv/bin/python scripts/audit_reachability.py   # module-level import reachability from each entry surface
```

---

## 0. High-level overview

The repo is **one verified spine with three execution surfaces** layered on top. The merge
brought main's *agentic / analysis* design alongside the branch's *graph-driven engine*; they
now coexist and partly overlap.

```
                         ENTRY POINTS (the "top")
        refactorika CLI                         MCP server (python -m refactorika.mcp_server)
        (refactorika/cli.py)                    (refactorika/mcp_server.py)
              │                                          │
   ┌──────────┼───────────────┐              ┌───────────┼──────────────────┐
   │          │               │              │           │                  │
 (default)  --agents      --show-*        run_pipeline  run_agents     analysis tools
   │          │                              │           │             analyze_file / find_duplicates
   ▼          ▼                              ▼           ▼             find_dead_code / generate_docs …
SURFACE A   SURFACE C                     SURFACE A   SURFACE C        SURFACE B
THE ENGINE  THE AGENT CAMPAIGN            (engine)    (agents)        ANALYSIS / MCP TOOLS
pipeline/   agents/ + analysis/audit                                  analysis/ + core/analyze
   │          │                                                          + core/apply + docs_gen
   │          │ ComplexityAgent ─► deterministic engine (decompose)      │
   │          │ DeadCode/Import  ─► transforms/{dead,imports} (legacy)    │
   └────┬─────┘                                                          │
        ▼                                                                ▼
   transforms/* (pure EditMap) ─────────► THE VERIFIED SPINE ◄── core/apply.py (legacy single path)
                                          pipeline/checker.py
                                          parse → ruff → pyright → pytest(impact-scoped)
                                          → git commit (green) / byte-for-byte revert (red)
                                                  │
                                          core/storage.py  ── Redis (primary) | JSON (fallback)
                                          memory/* (decision memory, vectors, codebase index)
```

- **Surface A — the engine** (`pipeline/orchestrator.run_pipeline`): graph-driven, deterministic
  + optional LLM decomposition, leaf-to-root, impact-scoped. The newest, most-tested path.
- **Surface B — analysis / MCP tools**: main's read-only analyzers (`analyze_file`,
  `find_duplicates`, `find_dead_code`, `generate_docs`, `get_context_map`) plus the single-file
  `apply_and_verify`. Exposed over MCP; **not** used by `run_pipeline`.
- **Surface C — the agent campaign** (`agents/orchestrator`): audit → dependency-ordered plan →
  specialist agents. Only `ComplexityAgent` is wired through the verified engine; the others
  still use main's legacy transform + `core/apply` path. See `--agents` / the `run_agents` tool.

All three commit through git and persist state to Redis (or the JSON fallback).

---

## 1. Entry points (the "top")

| Entry | Defined | Reaches |
|---|---|---|
| `refactorika <dir>` (console script) | `pyproject` → `refactorika.cli:main` | Surface A by default; Surface C via `--agents`; read-only views via `--show-graph/--show-plan/--show-memory/--show-similar` |
| `refactorika-scan` | alias → same `cli:main` | identical |
| `python -m refactorika.mcp_server` | `refactorika.mcp_server` | Surfaces A (`run_pipeline`, `build_graph`, `get_plan`), B (analysis tools), C (`run_agents`) |
| `eval/*.py`, `scripts/*.py` | run directly | the eval harness + Sentry/observability (see §7) |
| `pytest tests/` | — | everything, plus a few modules nothing else reaches (see §7) |

---

## 2. Reachability audit — is all the code used?

Module-level result (`scripts/audit_reachability.py`, 56 modules total):

| Class | Count | Meaning |
|---|---|---|
| **Product-reachable** (CLI + MCP) | 43 | imported transitively from a real front door |
| **Eval/scripts-only** | 3 | `dashboard.py`, `observability.py`, `harness.py` — used by the eval harness / Sentry scripts, **not** by the product |
| **Test-only** | 2 | `analysis/related.py` (+ the `llm/__init__` marker) — reachable only from a test |
| **Orphaned** | `transforms/move.py` + 7 empty `__init__.py` | nothing imports `move.py`; the `__init__` files are package markers (loaded implicitly, not dead) |

**Headline:** almost everything is reachable, but there are **four real "not fully used" spots**
(details + recommendations in §7): `transforms/move.py` (dead), `analysis/related.py`
(product-dead, test-only), `DuplicateAgent` (no-op stub), and the Sentry/observability +
dashboard code (wired only to eval/scripts, not the product — see the Sentry thread).

> Caveat: this is *module-level*. A module being reachable means it's imported, not that every
> function in it is called. Known function-level dead spots are listed in §7.

---

## 3. Surface A — the engine pipeline (`pipeline/orchestrator.run_pipeline`)

### 3.1 Build the graph — `graph/resolver.py` (`build_graph`)
Uses **Jedi** for real static name resolution (this *replaced* the old
`analysis/call_graph.py` heuristic for the engine — though `call_graph.py` is still alive in the
analysis layer, see §4):
- Pass 1: every `.py` → a `jedi.Script`; module-owned function/class defs become `Symbol` nodes
  (`graph/model.py`), keyed by module-qualified name (`orders.compute_total`).
- Entry points flagged textually: public top-level symbols, `__all__`, names called under
  `if __name__ == "__main__"`, test files / `test_*`, and route/command/fixture/task decorators.
- Pass 2: every reference (`n.goto(follow_imports=True)`) resolves to the symbol it points to →
  directed edge `src → dst` ("src depends on dst"). Module-level `import` edges tracked
  separately so they don't pollute impact analysis.

Result: a `Graph` (`symbols`, `edges`, `import_edges`, `entry_points`), dict-serializable for
Redis.

### 3.2 Order the work — `graph/order.py`
- **`topo_order`** — Tarjan SCC condensation then leaf-first emission (build on verified deps);
  cycles reported as groups.
- **`impact_of(graph, q)`** — reverse reachability: everything that transitively depends on `q`.
  This is the per-edit re-verification scope (the "impact-scoped tests" win).
- **`reachable_from(graph, roots)`** — forward reachability from entry points; the complement is
  the dead-code candidate set.

### 3.3 Plan — `pipeline/planner.py` + `pipeline/planner_llm.py`
Both return a `Worklist` of `PlanItem(spec, order_index, impact)`, so they're interchangeable.
- **`deterministic_plan`** (no LLM): dead-code removal of private symbols unreachable from any
  entry point, ordered **root-to-leaf**; then per-module `cleanup`, ordered last.
- **`llm_plan`** layers judgment on top:
  - Finds **god functions** — note the detector is a **three-axis union**, not a line count:
    cyclomatic complexity ≥ 6 **or** length ≥ 30 lines **or** control-flow nesting ≥ 4
    (`_is_god_function`, using `radon` + `analysis/parser.max_nesting_depth`).
  - Computes a structural fingerprint (`canonical_type_stream`, hashed), asks
    `DecisionMemory.recall` for a prior decision, and **reuses helper names** when one exists.
  - The per-function decision lives in **`decompose_item`** — the single source of truth shared
    by this planner *and* the `ComplexityAgent` (§5), so engine and agent decide identically.
  - No LLM reachable → returns the deterministic plan unchanged.
- `renames_first_planner` runs explicit `rope` renames before the base plan.

### 3.4 Orchestrate — `pipeline/orchestrator.py` (`run_pipeline`)
1. Copy repo to a temp dir unless `--apply`; ensure it's a git repo.
2. **Full suite as baseline** — must start green.
3. Build graph once; get a `Worklist`.
4. Per item: **rebuild the graph** (positions shift after each edit; writes are single-threaded),
   `dispatch(spec, root, graph)` → `EditMap`, skip if target gone / no edits.
5. Map `impact` → pytest node ids (`impacted_test_node_ids`), hand the `EditMap` to the `Checker`.
6. **Cascade dead-code to a fixpoint** (`_cascade_dead_code`, ≤25 rounds).
7. **Full suite as finale** — the authoritative "all N still pass".

### 3.5 Transform engines — `transforms/` (pure; return `EditMap`, never write)
Routed by `transforms/base.py:dispatch`:

| kind | engine | tool |
|---|---|---|
| `rename` | `rename.py` | `rope`, cross-file |
| `cleanup` | `cleanup.py` | `autoflake` + `ruff` |
| `remove_dead_code` | `dead_code.py` | LibCST removal |
| `decompose_function` / `extract` / `inline` / `change_signature` / `move` | `node_replace.py` | LibCST function-body replacement |

> ⚠️ Every kind in that last row routes to `node_replace` — including `move`. The separate
> **`transforms/move.py` is never imported** (see §7).

---

## 4. Surface B — analysis / MCP tools (main's read-only layer)

Exposed as MCP tools in `mcp_server.py`; **not** reached by `run_pipeline`. Backed by the
**`analysis/call_graph.py`** heuristic call graph (still alive here even though the engine
replaced it with Jedi):

| MCP tool | backing module |
|---|---|
| `analyze_file` | `core/analyze.py` |
| `find_duplicates` | `analysis/duplicates.py` (+ `memory/vector_index` for tier-2 semantic) |
| `find_dead_code` | `analysis/dead_code.py` → `analysis/call_graph.py` |
| `generate_docs` / `get_context_map` | `docs_gen.py` + `memory/context.py` |
| `apply_and_verify` / `apply_and_verify_multi` | `core/apply.py` (single verified-apply path; full-suite tests, graph-blind) |
| `audit_repo`-style planning | `analysis/audit.py` → `Plan` (also feeds Surface C) |

`analysis/related.py` (`find_related`) belongs here conceptually but **its MCP tool was dropped**
in the branch server, so nothing in the product calls it (§7).

---

## 5. Surface C — the agent campaign (`agents/orchestrator`)

`--agents` (CLI) and `run_agents` (MCP) call **`run_campaign`**: `analysis/audit.build_plan`
(audit → dependency-ordered `Plan`) → auto-confirm → **`dispatch_plan`**.

`dispatch_plan` builds the graph + one shared `Checker` from `plan.repo`, then for each task
(serialized; graph rebuilt before each) routes to a specialist by dominant `kind` and calls
`agent.handle(task, storage, graph=graph, checker=checker)`:

| agent | path | does real work? |
|---|---|---|
| **`ComplexityAgent`** | **verified engine**: `propose_specs` → `decompose_item` (LLM) → `dispatch` (`node_replace`) → `Checker.verify_apply` (impact-scoped) | ✅ when an LLM key is set; else a no-op |
| `DeadCodeAgent` | legacy text path: `analysis/dead_code` + `transforms/dead.py` → `core/apply` (full suite) | ✅ deterministic |
| `ImportAgent` | legacy text path: `transforms/imports.py` → `core/apply` | ✅ deterministic |
| `DuplicateAgent` | `propose()` returns the file unchanged | ❌ **no-op stub** |

`agents/base.SpecialistAgent.handle` is the seam: given `graph` + `checker` it takes the verified
engine path (via `propose_specs`); otherwise it falls back to `propose` → `core/apply`. Only
`ComplexityAgent` overrides `propose_specs` today, so the integration is **incremental**.

---

## 6. The shared verified spine, contracts, memory, storage

### 6.1 Verify & commit/revert — `pipeline/checker.py` (`Checker.verify_apply`)
Per edit, cheapest-first, short-circuit: **parse** (tree-sitter, before disk) → snapshot + write
→ **lint** (`ruff`, new violations only vs. baseline) → **type** (`pyright`, new errors only) →
**behavior** (`pytest`, impact-scoped node ids). All green → `git commit`; any red/crash →
byte-for-byte restore. Either way an `EditRecord` is appended to storage. Tools are the arbiter —
no LLM decides safety. (Surface B's `core/apply.py` is a parallel, older verified-apply that runs
the **full** suite and is graph-blind — see §7.)

### 6.2 Contracts — `core/schema.py`
`TransformSpec` (kind + target + params), `EditMap` (`{abs_path: contents}`), `EditRecord`,
`GateChecks`, `Plan`/`PlanTask`/`Opportunity` (Surface B/C), `Worklist`/`PlanItem` (Surface A),
`TRANSFORM_KINDS`. The frozen interface every surface shares.

### 6.3 Memory — `memory/decision_memory.py` + `agent_memory.py` (+ `vector_index.py`, `codebase_index.py`)
Not a cache — a record of judgment. Each `RefactorDecision` (pattern → kind → chosen helper
names) is stored keyed by an embedding of the code it acted on. `recall()` checks exact
structural-shape match first, then semantic similarity via the vector index above a **0.86**
cosine threshold. `codebase_index.py` embeds every symbol into a namespaced Redis vector space to
feed the decompose prompt real neighbor context (`--show-similar`).

### 6.4 Storage — `core/storage.py`
Redis primary (`REDIS_URL`, default `localhost:6379`), JSON fallback. Holds the edit log,
analysis cache, current plan, decision hash, and (via the vector index) the embeddings. Vector
search needs RediSearch (redis-stack); on plain Redis it degrades to brute-force JSON.

---

## 7. Dead / not-wired code & post-merge duplication

### 7.1 Genuinely dead — safe to remove
- **`transforms/move.py`** — zero importers; `dispatch` routes `"move"` to `node_replace`. Pure
  dead code. *Recommend: delete* (the `move` kind keeps working via `node_replace`).

### 7.2 Product-dead — alive only for a test
- **`analysis/related.py`** (`find_related`) — imported only by `tests/test_related.py`. The
  branch MCP server dropped the `find_related` tool, so no front door reaches it. *Recommend:
  re-expose it as an MCP tool, or remove it + its test.*

### 7.3 Wired but does nothing
- **`DuplicateAgent.propose`** returns the file unchanged — a `consolidate_duplicate` campaign
  task is a no-op today (the deterministic consolidate engine is deferred). *Recommend: convert
  to `propose_specs` once a consolidate engine exists, or mark explicitly unsupported.*

### 7.4 Reachable only from eval/scripts (not the product)
- **`observability.py`** (Sentry) — `init_sentry` is called only by `dashboard.py` and the eval
  benchmark scripts. **The CLI and MCP server never initialize Sentry**, and nothing loads
  `.env` on that path, so product runs send nothing. (main's MCP `main()` *did* call
  `init_sentry("mcp")`; the merge kept the branch server, which dropped it.)
- **`dashboard.py`** — no CLI/MCP entry point; reachable only via `scripts/`.
- **`harness.py`** — main's eval harness; used by `eval/*`.

These are "used" (by eval/scripts) but **not wired into the product**. *Recommend: if Sentry
should cover real runs, add `init_sentry(...)` + a `.env` load to the CLI/MCP `main()`.*

### 7.5 Post-merge duplication (two-of-everything — tech debt, all reachable)
The merge left parallel implementations that should converge:

| Concern | Engine (Surface A) | Legacy (Surface B/C) |
|---|---|---|
| verified apply | `pipeline/checker.py` (impact-scoped, graph-aware) | `core/apply.py` (full suite, graph-blind) |
| dead-code removal | `transforms/dead_code.py` (LibCST) | `transforms/dead.py` (agent path) |
| orchestration | `pipeline/orchestrator.py` | `agents/orchestrator.py` |
| planning | `pipeline/planner.py` (graph) | `analysis/audit.py` (audit) |
| dead-code analysis | `graph/order.reachable_from` (Jedi) | `analysis/dead_code.py` → `call_graph.py` |

The end-state is to migrate the agents fully onto the engine spine (route every specialist
through `propose_specs` → `dispatch` → `Checker`), after which the legacy column can retire.
`ComplexityAgent` is the first step done.
