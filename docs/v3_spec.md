# Refactorika — v3 Spec (the as-built engine)

> **This is the source of truth.** It describes the product as actually built and tested,
> not an aspiration. The earlier MCP-harness spec (`v2_spec.md`) is **superseded** — see
> the banner there. The design rationale this operationalizes is `refatorika_plan.md`.

## 1. What Refactorika is

Refactorika is a **graph-driven, verified refactoring engine** for Python. You point it at
a repo; it builds a reference-correct model of the whole program, plans a safe
dependency order, applies deterministic transforms, and proves nothing broke — committing
each verified change and reverting anything that fails. It runs as a **standalone CLI**
(primary) and as an **MCP server** (secondary, for use inside an agent like Claude Code).

It is judged on two things:

- **Properly (correctness).** Every change is *reference-correct* (computed from real name
  binding, not text matching) and *behavior-preserving* (gated by the test suite). Renames
  update every true reference and nothing that merely shares the name; dead code is removed
  only when reachability proves it dead; the LLM never hand-writes a cross-file diff.
- **Efficiently.** Leaf-to-root ordering means each step builds on already-verified code.
  Per-edit verification is **impact-scoped** — only the tests a change can affect run — with
  the full suite as the authoritative baseline and finale. The LLM is used only for
  judgment and emits compact specs, not files.

## 2. Division of labor

| Layer | Responsibility | Built from |
|---|---|---|
| **Graph** | whole-program world model: symbols + reference edges, real binding | Jedi static analysis (`graph/resolver.py`) |
| **LLM** | judgment only — which god function to split, how to name pieces | Anthropic, compact JSON specs (`llm/client.py`) |
| **Engines** | apply a `TransformSpec` reference-correctly across files | rope + LibCST + ruff/autoflake (`transforms/`) |
| **Checker** | the gate: parse → lint → types → tests, commit/revert | `pipeline/checker.py` (reuses `core/gates.py`) |
| **Memory** | the shared brain: graph, decisions, vectors | Redis Iris, local-JSON fallback (`memory/`, `core/storage.py`) |

The LLM proposes; deterministic engines apply; tools (not a second LLM) verify; Redis
remembers the decisions so the work stays consistent across the repo.

## 3. Architecture (as built)

```
   CLI: refactorika <dir> [--apply] [--llm]            MCP: build_graph · get_plan · run_pipeline
                      └────────────────────┬───────────────────────┘
                                           ▼
                          pipeline/orchestrator.py  (plain loop; parallel reads, single-threaded writes)
        ┌──────────────────┬───────────────────────┬───────────────────────┐
        ▼                  ▼                       ▼                       ▼
   graph/ (resolver,    planner.py /            transforms/             pipeline/checker.py
   model, order)        planner_llm.py          rename · cleanup ·      parse→ruff→pyright→
   Jedi binding,        leaf-to-root worklist   dead_code · node_       IMPACTED pytest,
   leaf→root + impact   of TransformSpecs       replace (rope/LibCST)   git commit / revert
                                           │
        ┌──────────────────────────────────┴─────────────────────────────┐
        ▼     Redis Iris (storage + memory) — JSON fallback always         ▼
   graph + order        decision memory (naming consistency)      vectors (dup / exemplars)
```

## 4. Module map (what exists)

```
refactorika/
├── cli.py                  # Typer CLI: refactorika <dir> [--apply|--show-graph|--show-plan|--llm]
├── mcp_server.py           # MCP tools: build_graph, get_plan, run_pipeline, get_log (+ v2 advisory tools)
├── metrics.py              # radon LOC/complexity + graph dead-code count; before/after
├── graph/
│   ├── resolver.py         # Jedi-based reference-correct graph builder (replaces the old regex call-graph)
│   ├── model.py            # Symbol + Graph (nodes, edges, entry points, serialization)
│   └── order.py            # Tarjan SCC leaf-to-root topo, impact_of (reverse), reachable_from
├── transforms/             # deterministic engines — the only code that mutates source
│   ├── rename.py           # rope cross-file rename-propagation (extracted without touching disk)
│   ├── cleanup.py          # autoflake + ruff --fix + ruff format
│   ├── dead_code.py        # LibCST surgical removal of one symbol
│   ├── node_replace.py     # LibCST function-node replacement (for decomposition)
│   └── base.py             # EditMap + kind dispatch
├── pipeline/
│   ├── orchestrator.py     # the plain loop: plan → dispatch → check; dead-code cascade; dry-run vs --apply
│   ├── planner.py          # deterministic plan (no LLM): dead-code + cleanup, ordered
│   ├── planner_llm.py      # LLM plan: god-function decomposition + decision-memory consistency
│   └── checker.py          # multi-file atomic apply + gate stack + impact-scoped tests + git
├── llm/
│   └── client.py           # Anthropic client with record/replay cache + stub seam + no-key fallback
├── memory/                 # agent_memory (context + decisions), vector_index, context (reused from v2)
└── core/                   # schema (contracts), gates, storage (Redis/JSON), apply (v2 single-file)
```

## 5. The transform contract

Engines never write to disk or commit. Each takes a `TransformSpec` (kind + target
qualname + params) and returns an **EditMap** (`{abspath: new_contents}`). The checker
writes the EditMap atomically, runs the gates, and commits or reverts. This is why a
rename is computed by rope but only *lands* if the test suite still passes.

`TransformSpec` kinds: `rename`, `cleanup`, `remove_dead_code`, `decompose_function`
(and `move`/`extract`/`inline`/`change_signature` routed through node-replacement for v1).

## 6. The verification model (the trust spine)

Per edit, in cheapest-first order, short-circuiting on failure:

1. **parse** — `tree-sitter` must parse the proposed contents (before touching disk).
2. **lint** — `ruff` (only *new* violations vs. a pre-edit baseline are rejected).
3. **types** — `pyright`, zero errors.
4. **tests** — `pytest`, **impact-scoped**: only the tests reachable from the changed
   symbol (via `impact_of`) run. Type-clean ≠ behavior-preserving; this is the real proof.

All green → `git commit`. Any red or a crash → restore every touched file byte-for-byte.
Around the whole run, the **full suite** runs once at **baseline** (the repo must start
green) and once at the **finale** ("all N tests still pass") — the authoritative backstop
for any edge the impact-scoped subset or the graph might miss.

## 7. Ordering rules

- **Refactoring** goes **leaf-to-root**: a symbol is refactored after its dependencies, so
  each step builds on already-verified code (Tarjan SCC handles cycles, reported as groups).
- **Dead-code removal** goes **root-to-leaf** (caller before callee): removing a dead leaf
  while a still-present dead caller references it would leave an undefined name. After the
  planned removals, a **cascade** re-runs reachability to a fixpoint (a removal can orphan a
  helper, then a constant).

## 8. Redis as the shared brain (not a cache)

Redis Iris (with a mandatory local-JSON fallback) holds live decision state, not just
cached results:

- **Graph + order** — the symbol graph and its leaf-to-root order are queryable state.
- **Decision memory** — every judgment the LLM makes is recorded as a `RefactorDecision`
  keyed by the *structural shape* of the code. Before decomposing a function, the planner
  **recalls** how an identical shape was handled before and **reuses the same helper names**.
  That recall is what keeps the 2nd, 5th, Nth similar function consistent — the engine
  remembers its own conventions instead of re-deciding per file. (`memory/agent_memory.py`)
- **Vectors** — per-function embeddings for duplicate detection and similar-refactor
  exemplars (reused from v2; optional `[semantic]` extra).

Kill Redis and everything degrades to `.refactorika/` files with identical results — the
engine never *depends* on it.

## 9. The LLM layer

`llm/client.py` wraps Anthropic with a **record/replay cache** (keyed by model+system+
prompt) so runs are reproducible and re-runs are free, plus a **stub seam** so tests and
demos run fully offline. With no API key and no cache hit, `complete_json` returns `None`
and the planner falls back to the deterministic plan — **the engine never depends on the
model being reachable**. Temperature is 0; the model returns structured specs, never diffs.

## 10. Front doors

- **CLI** (`refactorika <dir>`): dry-run on a throwaway copy by default — prints the
  leaf-to-root plan, each verified edit with its gate results, and a before/after metrics
  table (LOC, complexity, dead-code count) plus the baseline/finale suite status. `--apply`
  runs in place and commits. `--show-graph` / `--show-plan` inspect without running. `--llm`
  adds the judgment passes.
- **MCP** (`mcp_server.py`): `build_graph`, `get_plan`, `run_pipeline(apply=...)`, `get_log`
  — the same engine, driven from inside an agent. The v2 advisory tools (`find_duplicates`,
  `find_dead_code`, `generate_docs`, …) remain available.

## 11. Tested (offline, no Redis, no API key)

`pytest -q` is green with stubbed LLM/embedder. Highest-value coverage:

- **Resolver correctness** (`test_graph.py`): same-name disambiguation across modules,
  aliased imports, method dispatch, dead vs. test-reached, impact = reverse reachability,
  cycles reported.
- **Engines** (`test_transforms.py`): rename updates all sites and *only* the true binding,
  pure (no disk writes); cleanup; surgical dead-code; node replacement.
- **Spine** (`test_pipeline.py`): behavior-break caught and **reverted byte-for-byte**;
  impact-scoped test selection; demo repo regression (2 dead removals + cleanup, finale green).
- **LLM + memory** (`test_llm_planner.py`): decomposition flows through the gates and
  commits; **two identical-shape functions get the same helper names** via decision recall.

## 12. Deferred (honest roadmap)

Characterization/golden-master tests for the strong behavior proof; incremental graph
updates (today the graph is rebuilt per item — correct, but O(repo) each step); a fully
deterministic `consolidate` engine for cross-file duplicate merging; move/signature-change
as first-class engines; multi-language via tree-sitter/ast-grep; higher autonomy as the
verification strengthens.
