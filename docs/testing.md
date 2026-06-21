# Test suite

Both branches run **offline** with no Redis and no API key (stubbed LLM/embedder; storage falls back to
JSON). `main`: **264** tests across 43 files. `working`: **202** tests. Run with `make test` or
`.venv/bin/python -m pytest -q`.

Live tests that need real services (e.g. `test_hybrid_live.py`) self-skip unless `REDIS_URL` /
`OPENAI_API_KEY` are present; semantic tests skip if `sentence-transformers` isn't installed.

## What the key files cover

| File | Covers |
|---|---|
| `test_graph.py` [main] | Jedi resolver correctness: same-name disambiguation, aliased imports, method dispatch, dead vs test-reached, impact = reverse reachability, cycles. |
| `test_transforms.py` [main] | Engines: rename updates all + only true binding (pure, no disk writes); cleanup; surgical dead-code; node replacement. |
| `test_pipeline.py` [main] | The spine: behavior break caught and reverted byte-for-byte; impact-scoped test selection; demo-repo regression; finale green. |
| `test_llm_planner.py` [main] | LLM decomposition flows through gates + commits; two identical-shape functions get the same helper names (decision recall). |
| `test_decision_memory.py` / `test_codebase_index.py` [main] | Decision persistence + recall; semantic codebase index (offline fake embedder). |
| `test_checker.py` / `test_apply_multi.py` | Multi-file atomic apply, gate ordering, commit/revert. |
| `test_core.py`, `test_gates.py` | `analyze_file`; the parse/lint/typecheck/test gate primitives. |
| `test_harness.py` | `verify_edits` atomic gates, rollback, custom test command. |
| `test_call_graph.py`, `test_dead_code.py`, `test_duplicates.py`, `test_related.py`, `test_audit.py` | The heuristic analysis layer (call graph, dead-code confidence, structural+semantic dups, impact, repo audit/plan). |
| `test_agent_*` (driver, loop, tools, harness_tools, providers, schema, metrics, campaign, decompose, memory) | The four-arm benchmark agent: state machine, budgets, tool execution, provider HTTP, plan/result schemas, metrics, campaign rollback. |
| `test_full_system_bench.py` + `test_full_system_*_cases.py` + `test_full_system_case_registry.py` | Benchmark runner: hidden-oracle isolation, independent OFF/ON proposals, calibration, behavior/structure separation, the 49-case registry incl. 20-/100-file scale + stress grading. |
| `test_stress_cases.py`, `test_scale_cases.py`, `test_harness_tasks.py` | Stress/scale case structure; shared-patch task manifest. |
| `test_storage_plan.py`, `test_vector_index.py`, `test_agent_memory.py` | Storage (JSON offline + Redis optional), vector index (offline embedder), agent memory persistence. |
| `test_dashboard.py`, `test_docs_gen.py`, `test_observability.py`, `test_providers.py`, `test_confirm.py`, `test_plan.py` | Dashboard rendering; living-docs generation; Sentry scrubbing/capture; provider abstraction; plan confirm; dependency planning. |
| `test_refactorbench_adapter.py` [main] | RefactorBench scope classification, rename detection, offline LLM stub. |

`conftest.py` supplies fixtures (tmp repos, stub providers). Benchmark-only subset:

```bash
.venv/bin/python -m pytest -q tests/test_full_system_bench.py tests/test_full_system_case_registry.py \
  tests/test_full_system_behavior_cases.py tests/test_full_system_multifile_cases.py \
  tests/test_full_system_recovery_cases.py tests/test_stress_cases.py tests/test_harness_tasks.py
```
