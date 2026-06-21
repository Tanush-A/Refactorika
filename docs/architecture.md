# Architecture

The canonical architecture reference. It describes the system **as built** (package version
`0.2.0`), the verified spine that makes every change trustworthy, the ordering rules, the end-to-end
data flow, and the front doors. For a file-by-file API reference see
[module-reference.md](module-reference.md).

> **Two branches.** Refactorika ships as two branches that are two views of one product. The
> **`working`** branch is the **demo / agent-harness** (MCP server + agent campaign + four-arm
> benchmark). The **`main`** branch is the **v3 graph-driven engine** (Jedi graph + leaf-to-root
> pipeline + deterministic transform engines). **Read [branches.md](branches.md) first.** This doc
> describes the unified architecture; sections that exist only on one branch are tagged **[main]** or
> **[working]**, and the shared core is tagged **[both]**. The single most important shared idea —
> the **verified gate stack** (§5) — is identical on both.

---

## 1. The thesis

Refactoring is a **whole-program graph problem**. Four pieces collaborate:

- **The graph** is the world model: every symbol (module / class / function / method) and every
  *reference edge* between them, computed from **real name binding** (Jedi), not text matching.
- **The LLM** supplies *judgment only* — which god function to split, how to name the pieces — and
  emits compact JSON specs, never diffs.
- **The deterministic engines** (rope, LibCST, ruff/autoflake) *apply* a spec reference-correctly
  across every affected file. They return edited file contents; they never write to disk.
- **The test suite** is the arbiter of behavior. A change lands only if the impacted tests stay
  green; otherwise it is reverted byte-for-byte.

Two north stars: **properly** (reference-correct + behavior-preserving) and **efficiently**
(leaf-to-root order so each step builds on verified code; impact-scoped tests; token-lean LLM).

---

## 2. Division of labor (the five layers)

| Layer | Responsibility | Built from | Code |
|---|---|---|---|
| **Graph** | whole-program world model: symbols + reference edges, real binding, ordering, impact, reachability | Jedi static analysis | `graph/` |
| **LLM** | judgment only — decomposition decisions, naming — as compact JSON specs | Anthropic (or Ollama), temp 0, record/replay cache | `llm/` |
| **Engines** | apply a `TransformSpec` reference-correctly across files, returning an `EditMap` | rope + LibCST + ruff/autoflake | `transforms/` |
| **Checker** | the gate: parse → lint → types → tests, then commit or revert | tree-sitter + ruff + pyright + pytest + git | `pipeline/checker.py`, `core/gates.py`, `harness.py` |
| **Memory** | the shared brain: graph state, naming decisions, vectors | Redis (RedisVL), local-JSON fallback | `memory/`, `core/storage.py` |

The LLM proposes; deterministic engines apply; tools (not a second LLM) verify; Redis remembers the
decisions so the work stays consistent across the repo.

---

## 3. Component map

> This diagram shows the **`main`** v3 engine (it has every layer). On **`working`**, drop the
> `graph/`, `planner*`, and most of `transforms/`; the MCP server + `agents/orchestrator.py` +
> `core/apply.py` + `analysis/` + `memory/` provide the harness, and Claude (over MCP) supplies the
> judgment that the planner provides on `main`.

```
   CLI: refactorika <dir> [--apply|--llm|--agents|--show-*|--rename]      MCP: build_graph · get_plan · run_pipeline · run_agents · (+ v2 advisory tools)
                      └─────────────────────────────┬──────────────────────────────────┘
                                                    ▼
                  ┌──────────────────────────────────────────────────────────────────┐
                  │  pipeline/orchestrator.py        agents/orchestrator.py            │
                  │  (autonomous worklist loop)      (specialist campaign, --agents)   │
                  └───────────────┬───────────────────────────────┬───────────────────┘
        ┌──────────────────┬──────┴────────────┬──────────────────┴─────┐
        ▼                  ▼                    ▼                        ▼
   graph/ (resolver,   planner.py /         transforms/              pipeline/checker.py
   model, order)       planner_llm.py       rename · cleanup ·       parse → ruff → pyright →
   Jedi binding;       leaf-to-root         dead_code · node_        IMPACT-scoped pytest;
   leaf→root + impact  worklist of          replace (rope/LibCST/    git commit / revert
   + reachability      TransformSpecs       ruff)
        │                                                              │
        └──────────────────────────────────┬───────────────────────────┘
                                            ▼
                     memory/ + core/storage.py — Redis (RedisVL), JSON fallback always
            graph + order          decision memory (naming consistency)        vectors (dup / exemplars / codebase index)
```

There are **two orchestrators**, by design (see §8): the autonomous `pipeline/orchestrator.py`
(default CLI / `run_pipeline`) and the specialist `agents/orchestrator.py` (the `--agents` campaign /
`run_agents`). Both share the same graph, transforms, and verification spine.

---

## 4. The transform contract — [main]

> The full deterministic engine set below lives on **`main`**. On **`working`** only two transforms
> exist (`transforms/dead.py`, `transforms/imports.py`, driven by the agents); Claude supplies the
> rest of the "engine" by writing whole-file contents that go through `apply_and_verify`.

Every engine is **pure with respect to disk and git**. Each takes a `TransformSpec` and returns an
`EditMap`:

```python
TransformSpec(kind, target, params={}, rationale="")   # core/schema.py
EditMap = dict[str, str]                                # transforms/base.py  — {abspath: new_contents}
```

`transforms/base.py:dispatch(spec, root, graph)` routes by `spec.kind`:

| `kind` | Engine | Library | What it does |
|---|---|---|---|
| `rename` | `transforms/rename.py` | rope | cross-file rename of one binding, every true reference, no false positives |
| `cleanup` | `transforms/cleanup.py` | autoflake + ruff | remove unused imports/vars, apply safe fixes (`F,I,SIM,C4,UP`), format |
| `remove_dead_code` | `transforms/dead_code.py` | LibCST | surgically remove one top-level symbol, preserving formatting |
| `decompose_function`, `extract`, `inline`, `change_signature`, `move` | `transforms/node_replace.py` | LibCST | replace a function node with new statements (used for LLM decomposition) |

The checker writes the `EditMap` atomically, runs the gates, and commits or reverts. This is why a
rename is *computed* by rope but only *lands* if the test suite still passes. An empty `EditMap`
(`{}`) means "no change" and is treated as a no-op.

> **Note on `move`**: the dispatcher routes `kind="move"` to `node_replace`, not to the separate
> `transforms/move.py` (a rope-based move that is present but **not wired** into the dispatcher).
> Move/change-signature as first-class engines are on the deferred roadmap. See
> [module-reference.md](module-reference.md#transformsmovepy-orphaned).

---

## 5. The verified spine (the trust model) — [both]

This is the heart of the product and is **identical on both branches**. Per edit, in
**cheapest-first** order, short-circuiting on the first failure:

1. **parse** — `tree-sitter` must parse the proposed contents (before anything touches disk).
2. **lint** — `ruff`: only *new* violations vs. a pre-edit baseline are rejected (existing debt is
   tolerated).
3. **types** — `pyright`: zero new type errors.
4. **tests** — `pytest`, **impact-scoped**: only the tests reachable from the changed symbol (via
   `graph/order.py:impact_of`) run. Type-clean ≠ behavior-preserving; this is the real proof.

All green → `git commit`. Any red or a crash → **restore every touched file byte-for-byte**.

Around the whole run, the **full suite** runs once at **baseline** (the repo must start green — if
not, the run is aborted) and once at the **finale** ("all N tests still pass") — the authoritative
backstop for any edge the impact-scoped subset or the graph might miss.

There are **three** entry points into this spine, for three callers:

- **`core/apply.py`** `apply_and_verify` / `apply_and_verify_multi` — [both] the v2 verified-apply
  used by the MCP server and the agent campaign. Atomic write → gates → `git commit` or restore.
- **`pipeline/checker.py`** — [main] integrated with the leaf-to-root pipeline loop (storage, decision
  memory, impact-scoped tests). Used by `pipeline/orchestrator.py` and the `main` `agents/` layer.
- **`refactorika/harness.py`** `verify_edits(...)` — [both] a standalone, Redis-free contract used by
  the evaluation harness (`eval/`). Same gate order; tri-state results (`True`/`False`/`None`-skipped);
  it writes verified files but leaves the `git commit` to the caller.

All three delegate the per-language gate primitives to `core/gates.py` (via the `languages/` adapters).

---

## 6. Ordering rules — [main]

- **Refactoring** goes **leaf-to-root**: a symbol is refactored after its dependencies, so each step
  builds on already-verified code. `graph/order.py:topo_order` does a **Tarjan SCC** condensation and
  emits components leaf-first; mutually-recursive cycles are reported as groups so the orchestrator
  can handle an SCC together rather than guessing an impossible order.
- **Dead-code removal** goes **root-to-leaf** (caller before callee): removing a dead leaf while a
  still-present dead caller references it would leave an undefined name. After the planned removals, a
  **cascade** re-runs reachability (`reachable_from`) to a fixpoint — one removal can orphan a helper,
  then a constant, and so on.

---

## 7. End-to-end data flow (autonomous pipeline) — [main]

```
repo path
   │
   ▼  graph/resolver.py: build_graph()          → Graph (Jedi: symbols + reference edges + entry points)
   │
   ▼  graph/order.py: topo_order()              → leaf-to-root order + cycle groups
   │
   ▼  pipeline/planner.py  (deterministic)      → Worklist of TransformSpec  (dead-code + cleanup)
   │  or pipeline/planner_llm.py  (--llm)       → + god-function decomposition, with decision recall
   │
   ▼  pipeline/orchestrator.py: run_pipeline()
   │     for each PlanItem (leaf→root):
   │        rebuild graph         (positions/qualnames shift after each commit)
   │        dispatch(spec) ───────────────────► EditMap            (transforms/)
   │        impact_of(target) ────────────────► test scope         (graph/order.py)
   │        checker.verify_apply(edits) ──────► commit or revert   (pipeline/checker.py)
   │     _cascade_dead_code()                  → fixpoint
   │
   ▼  metrics.py: before/after  (radon LOC/complexity + dead-code count)
   │
   ▼  PipelineResult (records, metrics, baseline/finale suite status)
```

Dry-run (the default) does all of this on a **throwaway temp copy** of the repo and prints the plan,
each verified edit, and the metrics table. `--apply` runs in place and commits.

The `--agents` campaign (`agents/orchestrator.py:run_campaign`) instead builds an audit-driven `Plan`
(`analysis/audit.py`), auto-confirms it, and dispatches each task to a **specialist agent**
(import / dead-code / complexity / duplicate). Agents bring judgment and route through the same
deterministic engines + checker. See [agents-and-languages.md](agents-and-languages.md).

---

## 8. Orchestrators

There is a **pipeline orchestrator** [main] and an **agent-campaign orchestrator** [both]. They are
parallel paths over the same gate stack, not layers of each other.

| | `pipeline/orchestrator.py` [main] | `agents/orchestrator.py` [both] |
|---|---|---|
| Front door | default engine CLI, `--apply`, `--llm`; MCP `run_pipeline` | `working`: MCP `run_agents` / `fix --multi-agent`. `main`: `--agents` flag / MCP `run_agents` |
| Input | a `Worklist` of `TransformSpec` from a planner | a confirmed `Plan` of `PlanTask` from the repo audit (`analysis/audit.py`) |
| Judgment | optional LLM planner (decomposition) | per-kind **specialist agents** (import/dead-code deterministic; complexity is LLM on `main`, a **stub on `working`**) |
| Concurrency | single-threaded; rebuilds the Jedi graph before each item | `working`: **parallel waves** (ThreadPoolExecutor, `max_workers`). `main`: single-threaded, rebuilds graph per task |
| Verify via | `pipeline/checker.py` (impact-scoped tests) | `core/apply.py` on `working`; `pipeline/checker.py` on `main` |

Both are verified per edit. See [agents-and-languages.md](agents-and-languages.md) for the campaign
in detail.

---

## 9. Redis as the shared brain (not a cache) — [both]

Redis holds **live decision state**, not cached results. Three things live there (with a mandatory
local-JSON fallback in `.refactorika/state.json`):

1. **Graph + order** — the symbol graph and its leaf-to-root order are queryable state.
2. **Decision memory** [main] — every LLM judgment is recorded as a `RefactorDecision` and indexed by
   an **embedding of the code it acted on**. Before decomposing a function, the planner recalls the
   most semantically similar prior decision (exact structural match first, then vector similarity) and
   **reuses the same helper names**, so near-duplicates stay consistent across the repo.
   (`memory/decision_memory.py`; this module is `main`-only.)
3. **Vectors** — per-symbol embeddings for duplicate detection, similar-refactor exemplars, and the
   semantic codebase index (`memory/vector_index.py`, `memory/codebase_index.py`). When `redisvl` is
   present and Redis is live, the index uses hybrid BM25 + vector search; offline it falls back to a
   brute-force numpy cosine over JSON.

Kill Redis and everything degrades to `.refactorika/` files with identical results. The engine never
*depends* on Redis being reachable. Force offline with `REFACTORIKA_OFFLINE=1`. See
[configuration.md](configuration.md) for the full storage/offline contract.

---

## 10. The LLM layer (provider-agnostic) — [main]

> On **`working`** there is no in-process generation provider for refactoring: **Claude is the agent**
> and drives the tools over MCP. Only the *embedding* side exists in-process (`analysis/embeddings.py`,
> full implementation; OpenAI or sentence-transformers). The provider abstraction below is a **`main`**
> feature.

**Generation and embeddings are separate, swappable providers** (`llm/providers.py`), because
Anthropic has no embeddings API and the embedding backend must work regardless of the generation
provider.

- **Generation:** `REFACTORIKA_LLM_PROVIDER` = `anthropic` (Claude, default) | `ollama` (local).
- **Embeddings:** `REFACTORIKA_EMBED_PROVIDER` = `local` (all-MiniLM-L6-v2, default) | `ollama` | `openai`.

`llm/client.py` wraps the chosen generation provider with a **record/replay cache** keyed by
`(provider, model, system, prompt)` — so a recorded run replays identically for reproducible demos
and eval — plus a **stub seam** for fully-offline tests. With no provider reachable and no cache/stub
hit, `complete_json` returns `None` and the planner falls back to the deterministic plan: the engine
never depends on a model being reachable. Temperature 0; the model returns structured specs, never
diffs.

---

## 11. The front doors — [diverged]

The CLI and MCP surface differ by branch; the full, exact surface for both is in
[cli-and-mcp.md](cli-and-mcp.md). In brief:

- **`working` (demo):** MCP server is the primary door — `refactorika serve` (default), 13 tools
  (`analyze_file`, `find_duplicates`, `find_related`, `find_dead_code`, `apply_and_verify`,
  `apply_and_verify_multi`, `generate_docs`, `get_context_map`, `audit_repo`, `get_plan`,
  `confirm_plan`, `run_agents`, `get_log`). CLI: `refactorika scan <path>` and `refactorika fix <path>`.
  Plus `python -m scripts.demo` for the 30-second demo.
- **`main` (engine):** CLI is the primary door — `refactorika <dir>` (dry-run by default) with
  `--apply`, `--llm`, `--agents`, `--show-graph`, `--show-plan`, `--show-memory`,
  `--show-similar QUALNAME`, `--no-tests`, `--rename a.b=c`. MCP adds the engine tools `build_graph`,
  `get_plan`, `run_pipeline(apply=…)`, `run_agents(path)` alongside the advisory tools.

---

## 12. Observability

`refactorika/observability.py` is a **privacy-safe, fail-open** Sentry integration: errors are
captured only if `SENTRY_DSN` is set, and `scrub_event` strips all prompts, source, patches, and
paths before anything is sent (an allow-list of fields/tags). `refactorika/dashboard.py` renders the
edit log / audit / plan / campaign for human inspection (`python -m refactorika.dashboard`). Neither
is required for the engine to run.
