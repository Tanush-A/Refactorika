# eval/

Evaluation assets for Refactorika. See [../docs/11-benchmarks-and-eval.md](../docs/11-benchmarks-and-eval.md) for the full plan.

## Running the eval (collaborators start here)

One command does everything — creates a local venv, installs deps, fetches benchmark data, and runs the eval:

```bash
make eval          # full run (setup -> fetch -> eval)
```

Other entrypoints:

```bash
make setup         # just create eval/.venv and install deps
make fetch         # just fetch benchmark data into eval/external/
make eval-no-fetch # run using already-fetched data (no re-clone)
make benchmark     # Phase-0 benchmark harness + terminal report (no fetch needed)
make help          # list all targets
```

No `make`? Call the script directly: `bash eval/run_eval.sh`.

## Benchmark harness (Phase 0)

`make benchmark` runs the **same-proposer, harness OFF vs ON** benchmark and
prints the terminal report defined in
[../docs/12-benchmark-display-spec.md](../docs/12-benchmark-display-spec.md).
It needs no external data — it runs the synthetic proposer over `demo_repo/`.

What it produces today (Phase 0, no agent):

- **§1 Reliability** — `raw` / `lint_type` / `full` tier ablation: catch rate,
  broken-but-landed (severity-segmented), false-rejection, committed-unverified.
- **§2 Enhancement** — before→after structure of what landed.
- **§4c Code health** + **§4a comprehension proxy** (deterministic).
- **Calibration** — negative controls self-check the harness/grader.
- **Δ vs previous run** — tracked-KPI diff against `results/latest.json`.

Modules: `benchmark.py` (tiers + synthetic proposer + oracle grader + controls +
aggregation), `report.py` (renderer + diff), `substrates.py` (git-init'd repo
copies), `metrics_health.py` (4c/4a). Results persist to `results/` (gitignored).

```bash
python eval/run_eval.py --benchmark --trials 1 --seed 7
```

## Real-agent arms (Phase 1) — local model, free

`make benchmark-agent` adds the **same agent, harness OFF vs ON** ablation on top
of Phase 0, using a **local** OpenAI-compatible model (no API key, no cost):

- **no-harness arm:** the agent proposes once → edit applied raw → graded by the
  independent oracle (repo tests). The first broken edit ships.
- **harness arm:** the agent proposes → `apply_and_verify` (full gate stack,
  atomic commit/rollback) → on rollback the failure reason is fed back and the
  agent re-proposes (up to `--agent-max-retries`); exhausted retries → escalate
  (`skipped-needs-human`), never force-committed.

This fills the previously-`pending` sections with measured numbers: the **AGENT
HEADLINE** (correct-landed no-harness vs harness), **§3 Autonomy & Cost**
(completion, escalations, retries, tokens, wall time), and **§4b Cost**.

### Setup (one time)

```bash
brew install ollama
ollama serve &                 # OpenAI-compatible endpoint at :11434/v1
ollama pull qwen2.5-coder:7b
```

### Run

```bash
make benchmark-agent
# or with options:
python eval/run_eval.py --agent \
  --agent-model qwen2.5-coder:7b \
  --agent-endpoint http://localhost:11434/v1 \
  --agent-max-retries 2 --agent-tasks 3
```

If the endpoint is unreachable, Phase 1 is skipped and those sections fall back to
`pending` — Phase 0 still runs offline. Modules: `proposers.py` (local agent
client), `agent_bench.py` (the two arms + instrumentation). Note: a local 7B model
is slow (~1-2 min/task) and weaker than frontier models — which *strengthens* the
OFF-vs-ON story (more mistakes for the harness to catch).

## RefactorBench slice (Phase 2) — real unseen OSS repos

`--refactorbench` runs the **harness OFF vs ON** arms against real RefactorBench
tasks (flask/django/etc.) so the result generalizes beyond our own `demo_repo`
(kills the "circular benchmark" criticism). Renders **§4d**.

- **Agent:** Claude via the Anthropic API by default (frontier model needed — a
  local 7B scores ~0 on these hard tasks). Multi-file SEARCH/REPLACE edits.
- **Oracle:** RefactorBench's own **AST grader** (dependency-free — no need to
  install Django/Flask). Solve = grader passes.
- **Gates:** `parse` + `lint` only. The `type`/`test` gates are skipped here
  because RefactorBench ships repos *without* their dependencies, so pyright
  (unresolved imports) and pytest (can't import the package) would fail every
  edit. The report says this explicitly.
- **`broken edits shipped`** = edits that fail the harness gates (parse **or**
  lint). The no-harness arm ships them; the harness rejects/escalates them.

### Setup

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env      # gitignored; never commit
eval/.venv/bin/pip install anthropic
bash eval/fetch_benchmarks.sh                   # populates external/refactorbench
```

### Run

```bash
python eval/run_eval.py --refactorbench \
  --refactorbench-tasks 3 \
  --refactorbench-provider anthropic \
  --refactorbench-model claude-sonnet-4-5-20250929
# free/offline variant (expect ~0 solves): --refactorbench-provider local
```

Tasks are picked smallest-primary-file first. Without an API key (or with the
endpoint down) Phase 2 is skipped and §4d stays `pending`. Caveats: these are
hard multi-hop tasks (paper anchors: ~22% LM-agent, ~87% human), so solve-rate is
low; and with a small slice the per-run numbers are noisy (frontier models aren't
fully deterministic even at temperature 0). Module: `refactorbench.py`.

## Layout

- `run_eval.sh` — one-command runner: venv + deps + fetch + driver. Idempotent.
- `run_eval.py` — eval driver (curated-repo eval + RefactorBench smoke check; external-slice adapter is stubbed until the harness exists).
- `requirements.txt` — eval dependencies (tree-sitter, pyright, ruff, pytest).
- `fetch_benchmarks.sh` — clones external benchmark data into `external/` (gitignored).
- `external/` — **gitignored.** Fetched benchmark data (RefactorBench). Never committed (mixed/GPL upstream licenses).
- `.venv/` — **gitignored.** Local virtualenv created by `run_eval.sh`.
- `ground_truth.json` — **committed.** Ground truth for the curated demo repo (dominant variant, deviating files, call-site set, planted edits). This is the headline eval; it's ours, so it lives in git. *(Add once the demo repo exists.)*

## What is and isn't committed

| Asset | Committed? | Why |
|---|---|---|
| Curated demo repo + `ground_truth.json` | Yes | Ours; the primary eval source |
| Adapter / harness driver code | Yes | Ours |
| RefactorBench data | No (gitignored) | Bundles GPLv3 (Ansible) + other OSS copies; ~53MB |

## Quick start

```bash
bash eval/fetch_benchmarks.sh   # pull RefactorBench into eval/external/
```
