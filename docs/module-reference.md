# Module reference

File-by-file reference for `refactorika/`. Tags: **[both]**, **[main]**, **[working]**, **[diverged]**
(see [branches.md](branches.md)). Signatures are exact; engines return data and never touch disk unless
noted. Contracts live in `core/schema.py`.

## Core contracts — `core/schema.py` [diverged]
Dataclasses (all `to_dict`/`from_dict`): `Opportunity(kind, location, detail, rank)`,
`AnalysisResult(file, opportunities)`, `GateChecks(parse, lint, typecheck, tests)` (each `bool|None`),
`EditRecord(file, refactor_kind, checks, retries, status, failure_reason, diff, files)` with
`status ∈ {committed, rolled-back, skipped-needs-human}`, `SymbolRef`, `DuplicatePair`, `DeadSymbol`,
`ExportRef`, `ModuleContext`, `AuditEntry`, `RepoAudit`, `PlanTask(file, opportunities, dependents,
order)`, `Plan(repo, dominant_finding, tasks, confirmed, decision)`.
**`main` adds:** `TransformSpec(kind, target, params, rationale)`, `PlanItem(spec, order_index,
impact)`, `Worklist(items, cycles)`, `RefactorDecision(pattern, transform_kind, target, choice)`,
`PipelineResult(...)`, `ScoutReport`.

## `graph/` — the Jedi world model [main]
- **`model.py`** — `Symbol(qualname, name, kind, file, line, column, scope, is_private, is_exported,
  decorators)` and `Graph` (symbols dict, directed reference `edges`, `import_edges`, `entry_points`).
  Methods: `add_symbol/add_edge/add_import_edge/add_entry_point`, `outgoing/incoming`, `reverse_edges`,
  `call_sites`, `to_dict/from_dict`. Edge "A→B" = A references B; self-edges ignored.
- **`resolver.py`** — `build_graph(path) -> Graph`. Two passes via Jedi: (1) symbols (functions/classes/
  methods whose `full_name` belongs to the module) + entry-point detection (public top-level, `__all__`,
  `__main__` callees, `test_*`/test files, route/fixture/command-style decorators); (2) edges via
  `goto(follow_imports=True)`. Reads files, no writes; Jedi errors swallowed. Helpers: `collect_py_files`,
  `module_name`.
- **`order.py`** — `topo_order(graph) -> (order, cycles)` Tarjan SCC leaf-first (iterative);
  `impact_of(graph, qual) -> set` reverse reachability (test scope); `reachable_from(graph, roots) -> set`
  forward reachability (live set for dead code).

## `transforms/` — deterministic engines [diverged]
Output type `EditMap = dict[abspath, new_contents]` (`{}` = no-op). **`main`:** `base.py:dispatch(spec,
root, graph)` routes by kind → `rename.py` (rope cross-file rename; `rename_at(root,file,offset,name)`),
`cleanup.py` (`clean_source`: autoflake + ruff `F,I,SIM,C4,UP` + format), `dead_code.py`
(LibCST `remove_symbol_from_source`), `node_replace.py` (LibCST `replace_function_in_source`; handles
`decompose_function/extract/inline/change_signature/move`), `base.py:line_col_to_offset`. `move.py`
exists but is **orphaned** (dispatch routes `move`→`node_replace`).
**Both:** `dead.py:remove_dead_symbols(path, names) -> str` (tree-sitter byte-range removal incl.
decorators; used by `DeadCodeAgent`); `imports.py:reorder_imports(path) -> str` (stdlib→third-party→local,
dedup; used by `ImportAgent`).

## `pipeline/` — leaf-to-root engine [main]
- **`orchestrator.py`** — `run_pipeline(root, *, apply=False, planner=None, storage=None,
  run_tests=True, cascade=True) -> PipelineResult`. Dry-run on a temp copy unless `apply`; baseline full
  suite → per item: rebuild graph, `dispatch(spec)`, `impact_of` for test scope, `checker.verify_apply`
  → commit/continue; then dead-code cascade to fixpoint; finale full suite + before/after metrics.
- **`planner.py`** — `deterministic_plan(graph) -> Worklist` (dead-code + cleanup, ordered).
- **`planner_llm.py`** — LLM god-function decomposition with decision-memory recall/consistency;
  `decompose_item(...)` → `TransformSpec(decompose_function)`. God-function = complexity≥6 OR len≥30 OR
  nesting≥4.
- **`checker.py`** — multi-file atomic apply + gate stack (parse→ruff→pyright→impact-scoped pytest) +
  `git` commit/revert; baseline/finale; decision/agent memory.

## `core/` (besides schema) [both]
- **`analyze.py`** — `analyze_file(path, storage=None) -> AnalysisResult` (split_module >150 LOC,
  reorder_imports, split_function >30 LOC, flatten_nesting depth>3); AST-signature cached.
- **`gates.py`** — `parse_gate(content)`, `lint_gate(path, baseline)`, `pyright_baseline`,
  `typecheck_gate(path, baseline)` (new errors only), `test_gate(repo)` (exit 5 = skip). Returns
  `(bool|None, detail)`.
- **`apply.py`** — `apply_and_verify(path, new_content, kind, storage)` and
  `apply_and_verify_multi(edits, kind, storage)` → `EditRecord`. Atomic write → 4 gates → `git commit`
  or restore. The verified-apply used by MCP + agent campaign.
- **`storage.py`** — `Storage(redis_url=…, json_path=…)`; `.backend ∈ {redis, json}`;
  `append_log/get_log`, `cache_get/cache_set`, `save_plan/load_plan`, `vector_*`. Redis optional; JSON
  fallback always. `main` honors `REFACTORIKA_OFFLINE`.

## `analysis/` — heuristic layer [both]
- **`parser.py`** — shared tree-sitter front end: `get_tree`, `iter_functions/iter_symbols/iter_calls/
  iter_imports`, `function_text`, `max_nesting_depth`, `canonical_type_stream` (structural fingerprint).
- **`call_graph.py`** — `CallGraph.build(path)`; scoped name resolution (same-module → imported alias →
  project-wide *unambiguous* only); `entry_points`, `call_sites`, `dependents_of`, `edges_from`,
  `node_info`. Heuristic (documented false positives); on `main`, `graph/resolver.py` is the
  reference-correct replacement for correctness-critical work.
- **`dead_code.py`** — `find_dead_code(path, storage) -> dict` (reachability from entry points;
  confidence high/medium/low via privacy + reflection-name risk; AST-signature cached).
- **`duplicates.py`** — `find_duplicates(path, storage, vector_index, threshold=0.55) -> dict`. Tier 1
  structural (canonical-type-stream sha1, sim 1.0); Tier 2 semantic (embeddings, cosine). Picks a
  consolidation target (more call sites / more central).
- **`related.py`** — `find_related(path, storage, vector_index, k=5, symbol="", threshold=0.5)` —
  semantic neighbors + call-graph dependents (impact check).
- **`audit.py`** — `audit_repo(path, storage) -> RepoAudit`; `build_plan(path, storage) -> Plan`
  (fewest-dependents-first; persists).
- **`embeddings.py`** — **[working]** full impl: `available()`, `embed/embed_one`, `provider_dim()`;
  OpenAI `text-embedding-3-small` (1536) or `sentence-transformers` (384); `REFACTORIKA_EMBED=local`
  forces local. **[main]** a thin shim delegating to `llm/providers.py`.

## `memory/` [diverged]
- **`agent_memory.py`** [both] — `AgentMemory`: `put/get/all_context`, `put/get/all_decisions`,
  `history`; writes `.refactorika/context/<module>.md` sidebars (Extracted facts + "Needs Claude" prose).
- **`vector_index.py`** [both] — `VectorIndex` (RedisVL `FT.HYBRID` BM25+vector RRF when live; JSON/numpy
  brute-force cosine offline): `upsert`, `query`, `query_hybrid`, `module_filter`, `get_meta`, `drop`;
  `Neighbor(key, score, meta)`. `main` adds a namespace/provider API.
- **`context.py`** [both] — `ContextRetriever.relevant/conventions/dependents`.
- **`decision_memory.py`** [main] — `DecisionMemory.record/recall/all_decisions`; exact-shape match
  first, then vector similarity (threshold 0.86); reuses helper names for consistency.
- **`codebase_index.py`** [main] — `build_codebase_index`, `similar_symbols`, `codebase_vector_index`
  (namespace `codebase`, incremental by source SHA).

## `llm/` [main]
- **`client.py`** — `LLMClient(provider, cache_path, stub, replay_only)`: `complete_json(system,prompt)`
  (stub → cache → live), `cache_key`, `available`; record/replay cache keyed by (provider, model,
  system, prompt); returns `None` when nothing reachable (caller degrades).
- **`providers.py`** — generation (`AnthropicProvider`, `OllamaProvider`) and embeddings
  (`Local`/`Ollama`/`OpenAI`) abstractions + `get_generation_provider()`, `get_embedding_provider()`;
  `dim()`, `available()`.

## Front doors & ops
- **`cli.py`** [diverged] — `working`: `scan`/`fix`/`serve`. `main`: engine runner `refactorika <dir>
  [--apply --llm --agents --show-* --no-tests --rename]`. See [cli-and-mcp.md](cli-and-mcp.md).
- **`mcp_server.py`** [diverged] — FastMCP `refactorika`; 13 tools each (see [cli-and-mcp.md](cli-and-mcp.md)).
- **`harness.py`** [both] — `verify_edits(repo, edits, *, test_command=None, required_gates=(),
  retries=0, timeout=180) -> VerificationRecord`; tri-state gates; writes verified files but leaves the
  commit to the caller (the benchmark contract). `mark_escalated` → `skipped-needs-human`.
- **`metrics.py`** [main] — `repo_metrics(root)` (radon SLOC/LLOC/complexity + dead-symbol count),
  `metrics_delta`.
- **`docs_gen.py`** [both] — `generate_docs`, `get_context_map` (living module context, incremental).
- **`dashboard.py`** [both] — `render`/`render_audit`/`render_plan`/`render_campaign`; `python -m
  refactorika.dashboard`.
- **`observability.py`** [both] — `init_sentry`, `capture_exception`, `report_exceptions`,
  `scrub_event` (strips prompts/code/paths), `capture_benchmark_regression`. Fail-open; only active with
  `SENTRY_DSN`.
- **`languages/`** [both] — adapter registry; Python full, generic fallback skips gates. See
  [agents-and-languages.md](agents-and-languages.md).
- **`agents/`** [both] — specialist campaign. See [agents-and-languages.md](agents-and-languages.md).
