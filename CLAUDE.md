# CLAUDE.md — Refactorika (project memory)

> Self-contained context for every Claude Code session. `docs/v3_spec.md` is the full
> source-of-truth spec; this file is the fast orientation. Keep it short and current.

## What we're building
- **Product:** **Refactorika** — a **graph-driven, verified refactoring engine** for Python.
  Point it at a repo; it builds a reference-correct whole-program model, plans a safe
  dependency order, applies deterministic transforms, and proves nothing broke (commit each
  verified edit; revert anything that fails its tests). Python target, Python tool.
- **One-liner:** *Refactoring is a whole-program graph problem. The LLM brings judgment,
  deterministic engines bring correctness at scale, the graph connects them, the test suite
  proves behavior is preserved.*
- **Two north stars:** **properly** (reference-correct + behavior-preserving) and
  **efficiently** (leaf-to-root order, impact-scoped tests, token-lean LLM).
- **Two front doors:** standalone Typer CLI `refactorika <dir>` (primary) + MCP server (secondary).

## Architecture (as built — see docs/v3_spec.md §4 for the module map)
- **graph/** — `resolver.py` builds the symbol graph via **Jedi** static analysis (real name
  binding; replaces the old regex call-graph). `model.py` = Symbol/Graph. `order.py` = Tarjan
  SCC leaf-to-root topo + `impact_of` (reverse reachability) + `reachable_from` (dead code).
- **transforms/** — deterministic engines, the ONLY code that mutates source. `rename.py`
  (rope, cross-file, extracted without touching disk), `cleanup.py` (autoflake+ruff),
  `dead_code.py` (LibCST removal), `node_replace.py` (LibCST function replacement). Each takes a
  `TransformSpec`, returns an `EditMap` ({path: new_contents}); commits nothing.
- **pipeline/** — `orchestrator.py` (plain loop: plan→dispatch→check; dead-code cascade; dry-run
  copy vs `--apply`), `planner.py` (deterministic: dead-code + cleanup), `planner_llm.py` (LLM
  god-function decomposition + **decision-memory consistency**), `checker.py` (multi-file atomic
  apply + gate stack + impact-scoped tests + git commit/revert).
- **llm/client.py** — Anthropic, temp 0, **record/replay cache** + stub seam + no-key fallback.
- **memory/** + **core/storage.py** — Redis Iris (graph, decisions, vectors) with mandatory
  local-JSON fallback. **core/** = schema (contracts), gates, storage, apply (v2 single-file).

## The verified spine (trust + the demo)
Per edit, cheapest-first, short-circuit: **parse (tree-sitter) → ruff → pyright → pytest**.
Tests are **impact-scoped** (only tests reachable from the changed symbol). All green → `git
commit`; any red/crash → restore every file byte-for-byte. The **full suite** runs at
**baseline** (must start green) and **finale** ("all N still pass") as the authoritative backstop.

## Ordering rules
- **Refactor** leaf-to-root (build on verified deps). **Dead-code removal** root-to-leaf (caller
  before callee, else undefined name), then **cascade** reachability to a fixpoint.

## Redis = decision memory, not a cache (the differentiator)
Every LLM judgment is a `RefactorDecision` indexed by an **embedding of the code it acted on**.
Before decomposing, the planner **recalls the most semantically similar** prior decision (exact
shape first, then vector similarity) and **reuses the helper names** — so near-duplicates stay
consistent. Live store = Redis (`REDIS_URL`); JSON fallback for offline (`REFACTORIKA_OFFLINE=1`).
Inspect: `refactorika <dir> --show-memory`. (`memory/decision_memory.py`)

## Providers (provider-agnostic harness)
Generation (`llm/providers.py`: Anthropic | Ollama) and embeddings (local MiniLM | Ollama) are
SEPARATE — Anthropic has no embeddings API. Select via `REFACTORIKA_LLM_PROVIDER` /
`REFACTORIKA_EMBED_PROVIDER`. The record/replay cache (`llm/client.py`) is keyed by
*(provider, model, prompt)* so any provider is reproducible. Engine never depends on a model
being reachable (degrades to deterministic plan).

## Eval — RefactorBench (`eval/refactorbench.py`)
Runs the engine on 100 real OSS tasks; classifies + **declines out-of-scope** explicitly.
Reports three honest numbers. Base: 54.5% in-scope pass (6/11), 90.9% subtask completion,
89/100 declined. `make eval-smoke|eval-inscope|eval-ablation|eval-all`. Results in `eval/results/`.

## Commands
```bash
.venv/bin/refactorika demo_repo                 # dry-run: plan + verified edits + metrics
.venv/bin/refactorika demo_repo --show-graph    # symbol graph / entry points / dead code
.venv/bin/refactorika demo_repo --show-plan     # leaf-to-root worklist
.venv/bin/refactorika demo_repo --show-similar orders.compute_total  # semantic neighbors (needs embeddings)
.venv/bin/refactorika demo_repo --apply         # write in place + commit
.venv/bin/refactorika demo_repo --llm           # + LLM decomposition (needs ANTHROPIC_API_KEY)
.venv/bin/python -m pytest -q                   # offline; no Redis, no API key needed
```

## Operating principles
- **Correctness first, then efficiency.** Reference-correctness is the whole value — never
  regress the resolver's same-name disambiguation.
- **Engines stay pure** (return EditMap, never write/commit); the checker owns disk + git.
- **The engine never depends on the LLM or Redis being reachable** — both degrade gracefully.
- **Tests are the arbiter**, not a second LLM. Skipped gates recorded explicitly, never silent-passed.
- ruff line-length 100; `demo_repo`/`eval/external` excluded from our lint (fixtures/3rd-party).

## Status / parked
- Built + tested (93 passing offline): graph, transforms, checker, orchestrator, both planners,
  CLI, MCP, decision memory, semantic codebase index (`memory/codebase_index.py`: embeds every
  symbol into a namespaced Redis vector space; feeds the LLM decompose prompt real neighbor
  context; `--show-similar`). God-function detection is a complexity/length/nesting union, not a
  line count. Embeddings are provider-agnostic (local MiniLM | Ollama | OpenAI) via `llm/providers.py`,
  the single source of truth (`analysis/embeddings.py` is now a shim).
- Deferred: characterization tests; incremental graph (today rebuilt per item); deterministic
  cross-file `consolidate`; move/change-signature as first-class engines; multi-language.
- Out of scope: behavior/API changes, test generation, dependency edits, architectural rewrites.
