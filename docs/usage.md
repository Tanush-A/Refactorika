> **ℹ Scope:** This usage guide targets the **`main`** engine CLI. For the **`working`** demo (MCP server, `scan`/`fix`, the four-arm benchmark) see [cli-and-mcp.md](cli-and-mcp.md) and [evaluation.md](evaluation.md). Start: [docs/README.md](README.md).

# Using Refactorika — the complete how-to

Every way to run the tool and exercise all of its functionality. For *how it works
internally* (architecture, reachability), see [`pipeline.md`](pipeline.md).

There are **three ways to run it**, all over one verified spine:
- **A. The engine CLI** — `refactorika <dir>` (graph-driven, deterministic + optional LLM).
- **B. The MCP server** — the same engine + analysis tools, exposed to an agent/Claude.
- **C. The agent campaign** — `--agents` / `run_agents` (audit → plan → specialist agents).

---

## 1. Setup

### Fast path (one command)
```bash
bash scripts/warmup.sh
```
Idempotent: creates `.venv`, installs deps (`.[semantic]`), scaffolds `.env`, and brings up a
local Redis if one isn't running. Safe to re-run; never overwrites `.env`.

> ⚠️ warmup starts **plain `redis:8`**, which lacks the RediSearch module. The semantic
> **vector index** (decision-memory similarity, `--show-similar`) needs RediSearch — for that,
> start `redis-stack` instead (next section). Everything else works on plain Redis or the JSON
> fallback.

### Manual setup
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"        # engine + test/lint tools
.venv/bin/python -m pip install -e ".[semantic]"   # + embeddings (sentence-transformers, redisvl, openai)
```

### Redis (recommended: redis-stack, has RediSearch)
```bash
docker compose up -d redis        # redis-stack on :6379, Redis Insight UI on :8001
```
Redis is the live store for decision memory, vectors, the edit log, and the analysis cache.
**Without Redis the tool still runs** — it falls back to local JSON (`.refactorika/state.json`).
Inspect what's stored at the Insight UI (http://localhost:8001) or `--show-memory`.

### `.env` (gitignored)
```bash
REDIS_URL=redis://localhost:6379/0    # explicit -> warns loudly if Redis is down (vs silent JSON fallback)
ANTHROPIC_API_KEY=sk-ant-...          # enables --llm decomposition + the complexity agent
OPENAI_API_KEY=sk-...                 # only if REFACTORIKA_EMBED_PROVIDER=openai
REFACTORIKA_LLM_PROVIDER=anthropic    # anthropic | ollama
REFACTORIKA_EMBED_PROVIDER=local      # local (sentence-transformers) | ollama | openai
```

---

## 2. Configuration (environment variables)

| Var | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Live store. Unset+unreachable → silent JSON fallback; set+unreachable → loud warning. |
| `REFACTORIKA_OFFLINE` | unset | `=1` forces the JSON store (used by the test suite). |
| `REFACTORIKA_STATE` | `.refactorika/state.json` | Where the JSON fallback lives. |
| `ANTHROPIC_API_KEY` | — | Enables `--llm` / the complexity agent (god-function decomposition). |
| `REFACTORIKA_LLM_PROVIDER` | `anthropic` | Generation backend: `anthropic` \| `ollama`. |
| `REFACTORIKA_LLM_MODEL` | provider default | Override the generation model. |
| `REFACTORIKA_LLM_BASE_URL` | `http://localhost:11434` | Ollama base URL. |
| `REFACTORIKA_EMBED_PROVIDER` | `local` | Embeddings: `local` \| `ollama` \| `openai` (separate from generation). |
| `REFACTORIKA_EMBED_MODEL` | provider default | Override the embedding model. |
| `OPENAI_API_KEY` | — | Required only for `REFACTORIKA_EMBED_PROVIDER=openai`. |
| `SENTRY_DSN` | — | Optional errors-only telemetry (not wired into the CLI/MCP front doors today — see pipeline.md §7). |

The engine never *depends* on the LLM or Redis being reachable — both degrade gracefully.

---

## 3. Surface A — the engine CLI (`refactorika <dir>`)

The default run is a **dry-run on a temp copy** — nothing in your tree changes until `--apply`.

```bash
.venv/bin/refactorika demo_repo
```
Prints: the storage backend (`redis`/`json`), the baseline suite result, every verified edit
(dead-code removal + cleanup), before/after metrics, and the finale suite result.

### Inspect without running (each exits after printing)
```bash
.venv/bin/refactorika demo_repo --show-graph                    # symbol graph, entry points, dead code
.venv/bin/refactorika demo_repo --show-plan                     # leaf-to-root worklist of transforms
.venv/bin/refactorika demo_repo --show-memory                   # stored RefactorDecisions (from Redis)
.venv/bin/refactorika demo_repo --show-similar orders.compute_total   # nearest semantic neighbors (needs embeddings)
```

### Apply for real (commits each verified edit to git)
```bash
.venv/bin/refactorika demo_repo --apply
```

### Reference-correct rename (deterministic centerpiece — repeatable flag)
```bash
.venv/bin/refactorika demo_repo --rename orders.compute_total=calculate_order_total
.venv/bin/refactorika demo_repo --rename a.foo=bar --rename b.Baz.m=run --apply
```
Renames every *true* reference across the repo (via rope), never a same-named-but-unrelated symbol.

### Add LLM judgment (god-function decomposition, consistent naming via decision memory)
```bash
.venv/bin/refactorika demo_repo --llm            # needs ANTHROPIC_API_KEY (first run records to
                                                 # .refactorika/llm_cache.json; later runs replay offline)
.venv/bin/refactorika demo_repo --llm --apply
```

### Faster (skip the test gates)
```bash
.venv/bin/refactorika demo_repo --no-tests       # parse/lint/type still run; pytest gate skipped
```

Flags compose: `--llm --rename a.b=c --apply` runs renames + deterministic plan + LLM
decomposition, applied and committed.

---

## 4. Surface C — the agent campaign (`--agents`)

Runs main's specialist agents through the verified engine: **audit → dependency-ordered plan →
dispatch**. **Applies in place** (each edit gated + committed, failures reverted).

```bash
.venv/bin/refactorika <dir> --agents
.venv/bin/refactorika <dir> --agents --no-tests
```

What each agent does (see pipeline.md §5):
- **ComplexityAgent** → LLM god-function decomposition via the deterministic engine (**needs
  `ANTHROPIC_API_KEY`**; without it, it's a verified no-op).
- **DeadCodeAgent / ImportAgent** → deterministic dead-code removal / import reordering.
- **DuplicateAgent** → currently a no-op (consolidate engine deferred).

> Tip: the campaign mutates the repo in place. Run it on a clean git tree (or a copy) so you can
> review/revert the per-edit commits.

---

## 5. Surface B — the MCP server (drive it from Claude / an agent)

```bash
.venv/bin/python -m refactorika.mcp_server          # stdio MCP server

# register once with Claude Code (auto-uses whatever REDIS_URL your .env points at):
claude mcp add refactorika -- .venv/bin/python -m refactorika.mcp_server
```

Tools exposed:

| Tool | What it does |
|---|---|
| `build_graph(path)` | Symbol graph, leaf-to-root order, entry points, dead symbols, cycles (read-only) |
| `get_plan(path)` | The leaf-to-root worklist of transform specs (read-only) |
| `run_pipeline(path, apply=False)` | Full verified pipeline; dry-run by default, `apply=True` commits |
| `run_agents(path)` | The agent campaign (audit → plan → specialists), applies in place |
| `analyze_file(path)` | Ranked structural-refactor opportunities (read-only) |
| `find_duplicates(path, threshold=0.83)` | Exact + semantic duplicate functions (read-only) |
| `find_dead_code(path)` | Unreachable symbols by confidence (read-only) |
| `apply_and_verify(path, new_content, refactor_kind)` | Apply one file through the gate stack; commit or revert |
| `apply_and_verify_multi(edits, refactor_kind)` | Multi-file atomic apply + verify |
| `generate_docs(path)` / `get_context_map(path)` | Module context map (persisted to memory) |
| `get_log()` | The append-only edit log |

---

## 6. Memory & Redis inspection

```bash
.venv/bin/refactorika <dir> --show-memory     # stored RefactorDecisions
# Redis Insight UI:  http://localhost:8001   (keys: refactorika:memory:decisions, :log, :cache, refactorika:vec:*)
```
Decision memory makes repeated decompositions consistent: a structurally-identical function
reuses the prior run's helper names. Exact-shape recall always works; semantic recall needs an
embedding provider (the `[semantic]` extra) + RediSearch.

---

## 7. Evaluation & benchmarks (`make`)

```bash
make help                 # list targets
make install              # venv + [dev]
make fetch                # fetch RefactorBench into eval/external/ (~53MB, gitignored)
make eval-smoke           # 5 in-scope tasks (quick harness check)
make eval-inscope         # all in-scope tasks
make eval-ablation        # in-scope, decision-memory ON vs OFF
make eval-all             # every task (out-of-scope declined honestly)
make benchmark-full-agent # full-system OFF-vs-ON agent benchmark
```
Results land in `eval/results/` (committed — they're the reported numbers).

---

## 8. Tests

```bash
REFACTORIKA_OFFLINE=1 .venv/bin/python -m pytest -q     # offline; no Redis, no API key needed
```
The suite forces the JSON store and stubs the LLM/embeddings, so it's deterministic and free.

---

## 9. Audit & troubleshooting

```bash
.venv/bin/python scripts/audit_reachability.py   # which modules are reachable / dead (see pipeline.md §2)
```

| Symptom | Cause / fix |
|---|---|
| `storage=json` when you expected Redis | Redis not running → `docker compose up -d redis`; check `REDIS_URL`. |
| `--llm` / `--agents` does nothing | No `ANTHROPIC_API_KEY` in `.env` (or no cached responses). |
| `--show-similar` empty / semantic recall off | `[semantic]` extra not installed, or Redis lacks RediSearch (use `redis-stack`). |
| Decompositions inconsistent across files | Decision memory not persisting — confirm `storage=redis` (or a stable JSON path). |
| Agent search tool errors (`rg` not found) | Install ripgrep: `brew install ripgrep`. |
