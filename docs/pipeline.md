# Refactorika pipeline (as built)

How a run of `refactorika <dir>` actually moves through the codebase, file by file.
Source of truth for this doc is the code under `refactorika/`; if it disagrees with
`docs/v3_spec.md`, trust the code.

## End-to-end flow

```
graph.resolver.build_graph        (Jedi: real name binding -> symbol graph)
        |
graph.order.topo_order / reachable_from   (Tarjan SCC -> leaf-to-root order, dead-code reach)
        |
pipeline.planner.deterministic_plan       (dead-code removal + cleanup, ordered)
        + pipeline.planner_llm.llm_plan   (optional: god-function decomposition, layered on top)
        |
pipeline.orchestrator.run_pipeline        (the loop: rebuild graph -> dispatch -> verify -> next)
        |
transforms.base.dispatch -> {rename, cleanup, remove_dead_code, node_replace}   (pure: EditMap)
        |
pipeline.checker.Checker.verify_apply     (parse -> ruff -> pyright -> pytest(impact-scoped))
        |
   commit (git)  <—— all green
   revert (byte-for-byte restore)  <—— any gate red or crash
        |
cascade dead-code to fixpoint, then run_full_suite as the authoritative finale
```

## 1. Build the graph — `graph/resolver.py` (`build_graph`)

Not a regex call-graph (that's the old, abandoned `analysis/call_graph.py`). Uses
**Jedi** for real static name resolution:

- Pass 1: every `.py` file becomes a `jedi.Script`; module-owned function/class
  definitions become `Symbol` nodes (`graph/model.py`), keyed by module-qualified name
  (`orders.compute_total`, `billing.Invoice.total`).
- Entry points are flagged textually: public top-level symbols, names in `__all__`,
  names called inside `if __name__ == "__main__"`, anything in a test file or named
  `test_*`, and symbols decorated with route/command/fixture/task-style decorators.
- Pass 2: every reference (`n.goto(follow_imports=True)`) is resolved to the symbol it
  actually points to — handles imports, aliases, `self`/method dispatch — and recorded
  as a directed edge `src -> dst` meaning "src references dst" (dst is a dependency).
- Module-level `import` edges are tracked separately so they don't pollute impact
  analysis with every top-level constant reference.

Result: a `Graph` (`symbols`, `edges`, `import_edges`, `entry_points`) that can be
serialized to/from dict for Redis persistence.

## 2. Order the work — `graph/order.py`

Three traversals, all over the same graph:

- **`topo_order`** — Tarjan SCC condensation (handles mutual recursion as a unit) then
  a leaf-first emission: if A references B, B comes before A. This is the refactor
  order — build on already-verified dependencies. Cycles are reported as groups, not
  silently broken.
- **`impact_of(graph, q)`** — reverse reachability: every symbol that transitively
  depends on `q`. This becomes the re-verification scope after editing `q` — the
  source of the "impact-scoped tests" efficiency win.
- **`reachable_from(graph, roots)`** — forward reachability from entry points. Anything
  outside this set is a dead-code candidate.

## 3. Plan — `pipeline/planner.py` + `pipeline/planner_llm.py`

Both planners return the same contract: a `Worklist` of `PlanItem(spec, order_index,
impact)`, so a deterministic plan and an LLM-augmented plan are interchangeable.

- **`deterministic_plan`** (always available, no LLM):
  1. Dead-code removal — private symbols unreachable from any entry point
     (`reachable_from`). Ordered **root-to-leaf** (reverse of refactor order) so a
     still-present dead caller is removed before the dead callee it references —
     otherwise you'd delete a symbol something else still names.
  2. Per-module cleanup (unused imports, autoflake/ruff simplification) — ordered last,
     after every dead-code removal.
- **`llm_plan`** (`planner_llm.py`) layers judgment on top of the deterministic plan:
  - Finds "god functions" (top-level, ≥12 lines) as decomposition candidates.
  - For each, computes a **structural fingerprint** (`canonical_type_stream` over the
    AST, hashed) and asks `DecisionMemory.recall` whether a structurally-identical or
    semantically-similar function was decomposed before.
  - If a prior decision exists, the prompt instructs the LLM to **reuse the same helper
    names** — this is what keeps the 2nd, 5th, Nth similar function consistent instead
    of re-deciding per file.
  - Calls the LLM (`llm/client.py`, temp 0, record/replay cache) for the actual split;
    appends a `decompose_function` `PlanItem`; records the new decision back into
    memory.
  - If no LLM is reachable (`client.available()` is false), returns the deterministic
    plan unchanged — the engine never depends on the model.
  - `renames_first_planner` is a third planner variant: explicit, provably-complete
    renames (via `rope`, repo-wide) run before everything else in the base plan.

## 4. Orchestrate — `pipeline/orchestrator.py` (`run_pipeline`)

The plain loop:

1. Copy the repo to a temp dir unless `--apply` (dry-run never touches the real tree);
   ensure it's a git repo so per-edit commits work.
2. Run the **full test suite as baseline** — must start green, or "we kept it green" at
   the end is meaningless.
3. Build the graph once, get a `Worklist` from the chosen planner.
4. For each `PlanItem`, **rebuild the graph** (positions/qualnames shift after the
   previous edit) before dispatching — writes are single-threaded by construction, one
   item at a time.
5. `transforms.base.dispatch(spec, root, graph)` produces an `EditMap`; skip if the
   target symbol is already gone (removed by an earlier cascade/item) or no edits come
   back.
6. Map the item's `impact` set to pytest node ids (`impacted_test_node_ids`) and hand
   the `EditMap` to the `Checker`.
7. After the worklist, **cascade dead-code removal to a fixpoint** (`_cascade_dead_code`,
   max 25 rounds): removing one private dead symbol can orphan a helper, which orphans
   a constant — keep removing (one per round, against a fresh graph) until reachability
   stabilizes.
8. Run the **full suite again as the finale** — the authoritative "all N still pass"
   number, not just the impact-scoped subset used per-edit.

## 5. Transform engines — `transforms/` (pure, never write to disk)

All take a `TransformSpec` and return an `EditMap` (`{abs_path: new_contents}`); `{}`
means no-op. Routed by `transforms/base.py:dispatch`:

| kind | engine | tool |
|---|---|---|
| `rename` | `rename.py` | `rope` — cross-file, reference-correct |
| `cleanup` | `cleanup.py` | `autoflake` + `ruff` |
| `remove_dead_code` | `dead_code.py` | LibCST removal |
| `decompose_function` / `extract` / `inline` / `change_signature` / `move` | `node_replace.py` | LibCST function-body replacement (v1: all land as a local rewrite) |

## 6. Verify and commit/revert — `pipeline/checker.py` (`Checker.verify_apply`)

Per edit, **cheapest-first, short-circuit**:

1. **Parse** every proposed file's contents (tree-sitter) *before* touching disk —
   reject malformed output for free.
2. Snapshot originals, write the edits to disk.
3. **Lint** (`ruff`) — only *new* violations vs. a pre-edit baseline count as failure.
4. **Type** (`pyright`) — only *new* errors vs. a pre-edit baseline count.
5. **Behavior** (`pytest`) — **impact-scoped**: only test node ids reachable from the
   changed symbol's `impact` set run, not the whole suite.
6. Any gate red, or any exception during the gate stack: restore every touched file
   byte-for-byte from the snapshot, mark `rolled-back` with a `failure_reason`.
7. All green: `git add` + `git commit -m "refactor(<kind>): <files>"`, mark `committed`.
8. Either way, append the structured `EditRecord` to storage
   (`{file, refactor_kind, checks, retries, status, failure_reason, diff}`) — this is
   the per-edit log that powers the demo/dashboard.

Tools are the arbiter here — no LLM decides whether an edit is safe.

## 7. Memory — `memory/decision_memory.py` + `memory/agent_memory.py`

Not a cache — a record of *judgment calls*. Each `RefactorDecision` (pattern ->
transform kind -> chosen helper names) is stored keyed by an embedding of the code it
acted on. `recall()` checks exact structural-shape match first (cheap), then semantic
similarity via the vector index (`memory/vector_index.py`) above a 0.86 cosine
threshold. Backed by Redis live, local JSON offline (`REFACTORIKA_OFFLINE=1`) — recall
degrades to "no match found," never to an error.

## Two front doors, one engine

- CLI (`refactorika/cli.py`, primary): `refactorika <dir>`, `--show-graph`,
  `--show-plan`, `--apply`, `--llm`, `--show-memory`.
- MCP server (`refactorika/mcp_server.py`, secondary): same engine exposed as tools for
  Claude to drive inline.

Both call into the same `pipeline/orchestrator.run_pipeline` — there is exactly one
engine, not two implementations to keep in sync.
