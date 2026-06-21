# Configuration & operations

Everything you set to run Refactorika: environment variables, dependencies, the `Makefile` targets,
Docker, and the storage/offline contract. Differences between the **`working`** (demo) and **`main`**
(engine) branches are tagged. See [branches.md](branches.md).

---

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"          # engine + dev tools (pytest, pyright, ruff)
.venv/bin/python -m pip install -e ".[dev,semantic]" # + embeddings (sentence-transformers / redisvl / openai)
```

- Python **3.11+** (the harness and the target code).
- `[semantic]` is optional. Without it, structural duplicate detection and the gate stack still work;
  semantic similarity / vector search degrade gracefully (recorded as unavailable, never an error).
- The **benchmark** uses a separate venv at `eval/.venv` (created by `make setup` on `working` /
  `make install`-style flow on `main`) because its gate stack needs ruff + pyright + pytest isolated.

---

## Environment variables

Set these in a gitignored `.env` (loaded automatically via `python-dotenv`; existing process env is
never overridden). `.env.example` lists the supported keys per branch.

### Storage & memory — [both]

| Variable | Meaning | Default |
|---|---|---|
| `REDIS_URL` | Redis connection URL (`redis://…` or TLS `rediss://…`). Falls back to local JSON if unset/unreachable. | `redis://localhost:6379/0` |
| `REFACTORIKA_STATE` | Path to the local JSON fallback (edit log, cache, plan, vectors, decisions). | `.refactorika/state.json` |
| `REFACTORIKA_OFFLINE` | **[main]** Force the JSON backend even if `REDIS_URL` is set (CI/offline). Truthy = `true/1/yes/on`. | unset (online) |

### Generation provider (LLM judgment) — [main]

| Variable | Meaning | Default |
|---|---|---|
| `REFACTORIKA_LLM_PROVIDER` | `anthropic` (Claude) or `ollama` (local). | `anthropic` |
| `REFACTORIKA_LLM_MODEL` | Model id for the provider. | provider default (e.g. `claude-sonnet-4-6`) |
| `REFACTORIKA_LLM_BASE_URL` | Base URL for Ollama. | `http://localhost:11434` |
| `REFACTORIKA_LLM_API_KEY` | API key for the generation provider; falls back to `ANTHROPIC_API_KEY`. | unset |
| `ANTHROPIC_API_KEY` | Anthropic key (used when provider is `anthropic` and `REFACTORIKA_LLM_API_KEY` is unset). | unset |

> On **`working`** there is no in-process generation provider — Claude is the agent and drives the
> tools over MCP — so these LLM env vars do not apply. `ANTHROPIC_API_KEY` is still used by the
> **benchmark** runners (`eval/`) on both branches.

### Embedding provider (vectors, duplicate/semantic search) — [diverged]

**`main`** (provider abstraction in `llm/providers.py`):

| Variable | Meaning | Default |
|---|---|---|
| `REFACTORIKA_EMBED_PROVIDER` | `local` (all-MiniLM-L6-v2, 384-d) · `ollama` (nomic-embed-text, 768-d) · `openai` (text-embedding-3-small, 1536-d). | `local` |
| `REFACTORIKA_EMBED_MODEL` | Embedding model id. | provider default |
| `OPENAI_API_KEY` | Required when the embedding provider is `openai`. | unset |

**`working`** (`analysis/embeddings.py`, a full implementation):

| Variable | Meaning | Default |
|---|---|---|
| `REFACTORIKA_EMBED` | Set to `local` to force `sentence-transformers` (384-d). Otherwise OpenAI is used if `OPENAI_API_KEY` is set + `openai` is importable, else local. | unset |
| `OPENAI_API_KEY` | Enables OpenAI `text-embedding-3-small` (1536-d). | unset |

### Observability (optional, errors-only) — [both]

| Variable | Meaning | Default |
|---|---|---|
| `SENTRY_DSN` | Enables Sentry error capture (scrubbed of prompts/code/paths). Disabled if unset. | unset |
| `SENTRY_ENVIRONMENT` | Sentry environment tag. | `development` |
| `SENTRY_RELEASE` | Sentry release tag. | unset |

---

## Dependencies (`pyproject.toml`)

`name = "refactorika"`, `version = "0.2.0"`, `requires-python = ">=3.11"`. Console scripts:
`refactorika` and `refactorika-scan`, both → `refactorika.cli:main`.

**Core runtime** (both branches): `anthropic[mcp]` (FastMCP + Claude SDK), `tree-sitter` +
`tree-sitter-python`, `redis`, `numpy`, `sentry-sdk`.

**`main` adds the engine libraries**: `libcst` (node replacement / surgical removal), `rope`
(reference-correct rename/move), `jedi` (the symbol graph), `autoflake` (cleanup), `radon` (metrics),
`typer` (the engine CLI shell).

**`[semantic]` extra** (both): `sentence-transformers` (local embeddings), `redisvl` (RedisVL hybrid
index), `openai` (OpenAI embeddings).

**`[dev]` extra**: `pytest`, `pyright`, `ruff` (+ `fakeredis` on `main`).

**Tooling config**: pyright `strict`, target 3.11, include `["refactorika"]`. ruff `line-length=100`,
`target-version=py311`, `select=["E","F","I"]`, source `["refactorika"]` (on `main`, `extend-exclude`
covers `demo_repo` and `eval/external` — fixtures and third-party code excluded from our lint).

---

## Makefile targets

### `working` (demo)

| Target | Runs | Purpose |
|---|---|---|
| `make setup` | `bash eval/run_eval.sh --setup` | create `eval/.venv` and install benchmark deps (required before benchmarks) |
| `make fetch` | `bash eval/fetch_benchmarks.sh` | clone RefactorBench into `eval/external/` (gitignored) |
| `make eval` | `bash eval/run_eval.sh` | setup → fetch → `run_eval.py` plumbing checks |
| `make eval-no-fetch` | `bash eval/run_eval.sh --no-fetch` | run eval using already-fetched data |
| `make benchmark` | `python -m eval.harness_bench --calibrate-only` | calibrate the shared-patch ablation (no model calls) |
| `make benchmark-agent` | `python -m eval.harness_bench …` | run the shared-patch ablation with a model |
| `make benchmark-full-calibrate` | `python -m eval.full_system_bench --calibrate-only` | validate all full-system case baselines (no model) |
| `make benchmark-full-agent` | `python -m eval.full_system_bench …` | **the primary product benchmark** (the four arms) |
| `make test` | `python -m pytest -v tests` | run the unit/integration suite |
| `make clean-eval` | `rm -rf eval/.venv` | remove the benchmark venv |

### `main` (engine)

| Target | Runs | Purpose |
|---|---|---|
| `make install` | venv + `pip install -e ".[dev]"` | set up the project |
| `make fetch` | `bash eval/fetch_benchmarks.sh` | fetch RefactorBench (~53 MB, gitignored) |
| `make eval-smoke` | `eval/run_eval.py --smoke` | RefactorBench: 5 in-scope tasks (harness check) |
| `make eval-inscope` | `eval/run_eval.py --in-scope` | RefactorBench: all in-scope tasks |
| `make eval-ablation` | `eval/run_eval.py --in-scope --ablation` | in-scope, decision-memory ON vs OFF |
| `make eval-all` | `eval/run_eval.py --all` | every task; out-of-scope declined honestly |
| `make benchmark*` / `make benchmark-full-*` | (same as `working`) | the harness + full-system benchmarks |
| `make test` | `pytest -v tests` | run the suite |

See [evaluation.md](evaluation.md) for the exact benchmark commands and flags.

---

## Docker — [main] (`docker-compose.yml`)

`main` ships a compose file; `working` runs Redis however you like (the project notes a local Docker
`redis:8` on `:6380` worked for the demo). The compose file provides:

- **`redis`** — `redis/redis-stack:latest` (RediSearch for the vector/hybrid index + Redis Insight UI),
  ports `6379` (Redis) and `8001` (Insight), with a persistent volume.
- **`agent-memory-server`** (profile `memory`) — optional Redis-Iris agent-memory service.

```bash
docker compose up -d redis                 # local redis-stack
docker compose --profile memory up -d      # + agent memory server
```

Then point `REDIS_URL` at it (e.g. `redis://localhost:6379/0`).

---

## Storage & offline contract — [both]

`core/storage.py` chooses a backend at construction:

1. If `REFACTORIKA_OFFLINE` is truthy **[main]**, or `REDIS_URL` is unset/unreachable → **JSON**
   backend at `REFACTORIKA_STATE` (default `.refactorika/state.json`). `.backend == "json"`.
2. Otherwise → **Redis** backend. `.backend == "redis"`.

Either way the API is the same: `append_log`/`get_log`, `cache_get`/`cache_set` (AST-signature keyed),
`save_plan`/`load_plan`, `vector_upsert`/`vector_get_all`/`vector_delete_all`. **Redis is an
optimization, never a hard dependency** — kill it and everything degrades to JSON files with identical
results (vectors fall back to brute-force numpy cosine; hybrid BM25 search is unavailable offline).

`.refactorika/` also holds human-readable artifacts: `context/<module>.md` (living docs) and, on
`main`, `llm_cache.json` (the record/replay LLM cache).
