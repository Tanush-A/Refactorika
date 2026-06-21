# Refactorika

A **graph-driven, verified refactoring engine** for Python. Point it at a repo; it builds a
reference-correct model of the whole program, plans a safe dependency order, applies
deterministic transforms, and **proves nothing broke** — committing each verified change and
reverting anything that fails its tests.

The pitch: *refactoring is a whole-program graph problem, not a per-file one.* The LLM brings
judgment, deterministic engines bring correctness at scale, the graph connects them, and the
test suite proves behavior is preserved.

## What makes it correct

- **Real reference resolution, not regex.** A symbol graph built from Jedi static analysis: a
  rename updates *every true reference and nothing that merely shares the name*; dead code is
  removed only when reachability proves it dead.
- **Deterministic engines own the edits.** rope (cross-file rename), LibCST (node replacement,
  dead-code removal), ruff + autoflake (cleanup). The LLM emits compact specs, never diffs.
- **Verified, then committed.** Every edit passes **parse → ruff → pyright → pytest** (tests
  *impact-scoped* to what the change can affect) before `git commit`; any failure reverts the
  files byte-for-byte. The full suite gates the run at **baseline** and **finale**.
- **Efficient by construction.** Leaf-to-root ordering means each step builds on already-verified
  code; only impacted tests run per edit.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

# Dry-run on the bundled messy repo (no changes written): see the plan, the verified
# edits (dead code removed + cleanup), and the before/after metrics.
.venv/bin/refactorika demo_repo

# Inspect without running:
.venv/bin/refactorika demo_repo --show-graph     # the symbol graph, entry points, dead code
.venv/bin/refactorika demo_repo --show-plan      # the leaf-to-root worklist

# Apply in place (commits each verified edit to git):
.venv/bin/refactorika demo_repo --apply

# Reference-correct rename across the whole repo (the centerpiece) — deterministic:
.venv/bin/refactorika demo_repo --rename orders.compute_total=calculate_order_total

# Add LLM judgment: god-function decomposition with consistent naming via decision memory.
# The first run with ANTHROPIC_API_KEY records responses to .refactorika/llm_cache.json;
# subsequent runs replay that cache offline (no key needed).
.venv/bin/refactorika demo_repo --llm

# Tests (offline — no Redis, no API key needed):
.venv/bin/python -m pytest -q
```

## Run as an MCP server (use inside an agent)

```bash
.venv/bin/python -m refactorika.mcp_server   # stdio MCP; tools: build_graph, get_plan, run_pipeline, get_log
```

## Providers, memory, and evaluation

- **Provider-agnostic LLM** — generation via Claude or local **Ollama**, embeddings via local
  MiniLM or Ollama (separate, since Anthropic has no embeddings API). Selected by env
  (`REFACTORIKA_LLM_PROVIDER`, `REFACTORIKA_EMBED_PROVIDER`); a record/replay cache makes any
  provider reproducible. See `.env.example`.
- **Redis as the shared brain** — decisions are stored in Redis (`REDIS_URL`, e.g. Redis Cloud)
  and recalled by semantic similarity so refactors stay consistent; local-JSON fallback for
  offline (`REFACTORIKA_OFFLINE=1`). Inspect with `refactorika <dir> --show-memory`;
  `docker compose up -d redis` runs a local instance.
- **RefactorBench eval** — `make fetch && make eval-inscope` runs the engine on real OSS
  refactoring tasks; results in `eval/results/`. See `docs/11-benchmarks-and-eval.md`.

## How it works

```
CLI / MCP  →  orchestrator  →  graph (Jedi)  →  planner (+LLM judgment)  →  engines (rope/LibCST/ruff)
                                                                              →  checker (gates + git)
                              Redis Iris = graph + decision memory + vectors (local-JSON fallback)
```

## Layout

```
refactorika/graph/       reference-correct symbol graph + leaf-to-root order + impact
refactorika/transforms/  deterministic engines (rename, cleanup, dead_code, node_replace)
refactorika/pipeline/    orchestrator · planner · planner_llm · checker
refactorika/llm/         Anthropic client with record/replay cache + stub seam
refactorika/memory/      Redis Iris: agent/decision memory, vectors (JSON fallback)
refactorika/core/        schema · gates · storage
refactorika/cli.py       standalone Typer CLI      refactorika/mcp_server.py   MCP server
demo_repo/               deliberately messy target + its tests
docs/v3_spec.md          the source-of-truth spec (as built)
```

Python only; behavior-preserving structural refactors. See `docs/v3_spec.md` for the full spec
and `CLAUDE.md` for project memory.
