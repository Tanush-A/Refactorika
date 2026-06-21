# The two branches: `working` (demo) and `main` (v3 engine)

> **Read this before anything else.** Refactorika exists as **two branches that are two views of one
> product**, and they have **materially different code**. Knowing which one you're on prevents hours
> of confusion (e.g. "why is there no `graph/` package?" or "why does the CLI take subcommands?").

## TL;DR

| | **`working`** (origin/working) | **`main`** |
|---|---|---|
| Role | **The demo branch — what the team runs.** | The v3 engine track. |
| Product framing | "An **agent harness delivered as an MCP server**" — Claude proposes, the harness verifies. | A "deterministic, **graph-driven refactoring engine**" the harness rides on. |
| Front door | MCP server (`refactorika serve`) + `scan`/`fix` CLI + `scripts/demo.py` | Engine CLI `refactorika <dir> [--apply|--llm|--agents|…]` |
| Headline artifact | The **four-arm full-system benchmark** (RAW / HARNESS / AGENTIC RAW / AGENTIC HARNESS) | The verified leaf-to-root pipeline + RefactorBench adapter |
| Tests | 202 | 264 |

Both descend from a common ancestor (`546145d`); `working` adds one commit (`devpost part 2`) on
top of that ancestor, while `main` carries the entire "Phase A–H" v3 engine lineage plus later work.
The polished **product narrative ([devpost.md](devpost.md)) describes the union of both** — the
agent harness *and* the graph engine — as one product.

## Which branch has which code

Everything below is **package-level** (`refactorika/…`). The evaluation harness in `eval/agents/`
(the four-arm benchmark) and `eval/full_system_bench.py` are **byte-identical on both branches**.

### On both branches (identical or near-identical)
- `harness.py` — the standalone `verify_edits()` gate contract used by the benchmark.
- `core/{analyze,apply,gates,storage,schema}.py` — the v2 analysis + verified-apply core. (`schema.py`
  is a strict subset on `working`; see below.)
- `analysis/{audit,call_graph,dead_code,duplicates,related,parser}.py` — the heuristic analysis layer.
- `languages/{base,registry,python_adapter,generic_adapter}.py` — the language-adapter registry.
- `agents/{base,orchestrator,complexity_agent,dead_code_agent,duplicate_agent,import_agent}.py` —
  the specialist agent campaign. **(The wiring differs between branches — see below.)**
- `memory/{agent_memory,context,vector_index}.py` — Redis-Iris memory + JSON fallback.
- `transforms/{dead,imports}.py` — the two tree-sitter transforms used by the agents.
- `dashboard.py`, `docs_gen.py`, `observability.py`, `mcp_server.py`, `cli.py` — present on both, but
  **`mcp_server.py` and `cli.py` diverge substantially** (see below).
- `eval/agents/*`, `eval/full_system_bench.py`, `eval/full_system_cases/*`, `eval/harness_bench.py`,
  `eval/harness_tasks.py` — the benchmark suite.

### `main`-only (the v3 graph engine — absent on `working`)
- `graph/{model,order,resolver}.py` — the **Jedi** reference-correct symbol graph, Tarjan leaf-to-root
  ordering, impact analysis, reachability.
- `pipeline/{orchestrator,planner,planner_llm,checker}.py` — the autonomous leaf-to-root pipeline and
  its integrated checker.
- `llm/{client,providers}.py` — the provider-agnostic LLM harness + record/replay cache.
- `transforms/{base,rename,cleanup,dead_code,node_replace,move}.py` — the full deterministic engine set
  (rope rename, LibCST node replace, etc.).
- `memory/{decision_memory,codebase_index}.py` — semantic decision memory + the codebase vector index.
- `metrics.py` — radon before/after metrics.
- `eval/refactorbench.py` — the RefactorBench adapter.
- The expanded `core/schema.py` contracts: `TransformSpec`, `PlanItem`, `Worklist`, `RefactorDecision`,
  `PipelineResult`, `ScoutReport` (these do **not** exist on `working`).

## How the same files differ between branches

| File | On `working` | On `main` |
|---|---|---|
| `cli.py` | Subcommand CLI: `refactorika serve` (default = MCP stdio server), `refactorika scan <path>`, `refactorika fix <path> [--dry-run --kinds imports,dead --multi-agent]`. | Engine CLI: `refactorika <dir> [--apply --llm --agents --show-graph --show-plan --show-memory --show-similar --no-tests --rename]`. |
| `mcp_server.py` | 13 tools incl. `find_related`, `audit_repo`, `confirm_plan`, `run_agents(max_workers)`. No `build_graph`/`run_pipeline`. | 13 tools incl. `build_graph`, `run_pipeline(apply)`, `run_agents(path)`. No `find_related`/`audit_repo`/`confirm_plan`. |
| `agents/complexity_agent.py` | **Stub** — `propose()` returns the file unchanged (no LLM wired). | Live — LLM-driven god-function decomposition through the deterministic engine + checker. |
| `agents/duplicate_agent.py` | Stub (no-op). | Stub (no-op). *(Same on both — consolidation is on the roadmap.)* |
| `agents/orchestrator.py` | `dispatch_plan(storage, max_workers)` runs tasks in **parallel waves** (ThreadPoolExecutor), verified via `core/apply.py`. | `dispatch_plan(...)` is **single-threaded**, verified via `pipeline/checker.py`; rebuilds the Jedi graph before each task. |
| `analysis/embeddings.py` | **Full implementation** — OpenAI `text-embedding-3-small` (1536-dim) with a `sentence-transformers` (384-dim) keyless fallback; `REFACTORIKA_EMBED=local` forces local. | A **thin shim** delegating to `llm/providers.py` (the single source of truth on `main`). |
| `memory/vector_index.py` | RedisVL `FT.HYBRID` (BM25 + vector, RRF) with JSON/numpy brute-force fallback. | Same hybrid index, with a namespace/provider API used by decision-memory + codebase-index. |
| `core/schema.py` | The advisory/verify contracts only. | Adds the v3 pipeline contracts. |
| `Makefile` | `setup`, `fetch`, `eval`, `eval-no-fetch`, `benchmark`, `benchmark-agent`, `benchmark-full-calibrate`, `benchmark-full-agent`, `test`, `clean-eval`. | `install`, `fetch`, `eval-smoke`, `eval-inscope`, `eval-ablation`, `eval-all`, plus the benchmark targets. |
| `.env.example` | `REDIS_URL`, `REFACTORIKA_STATE`, `SENTRY_*`, `OPENAI_API_KEY`, `REFACTORIKA_EMBED`. | Adds the provider env: `REFACTORIKA_LLM_PROVIDER/MODEL/BASE_URL/API_KEY`, `ANTHROPIC_API_KEY`, `REFACTORIKA_EMBED_PROVIDER/MODEL`, `REFACTORIKA_OFFLINE`. |

## Which one should I use?

- **Running the demo or the four-arm benchmark** → `working`. It is the branch the demo, the MCP
  server experience, and the published benchmark numbers come from. See
  [evaluation.md](evaluation.md) and [cli-and-mcp.md](cli-and-mcp.md).
- **Working on the graph-driven verified pipeline** (Jedi graph, leaf-to-root order, rope/LibCST
  engines, decision memory, RefactorBench) → `main`. See [architecture.md](architecture.md) and
  [module-reference.md](module-reference.md).

Throughout the rest of these docs, content is tagged **[both]**, **[main]**, **[working]**, or
**[diverged]** wherever the branch matters.
