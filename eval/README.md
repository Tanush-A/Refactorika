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
make help          # list all targets
```

## Full-system benchmark

This is the product-level comparison. Both arms start from separate copies of
the same case repository and receive the exact initial user request
`refactor this codebase`:

- **OFF** asks the model to inspect, select, and implement a refactor in one
  autonomous model call.
- **ON** has Refactorika audit the repository and build a dependency-ordered,
  architecture-aware prompt. The model proposes an independent patch, which is
  routed through atomic gates and up to two diagnostic-guided repairs.

The grader then injects hidden tests and independently checks the intended
structure. Hidden tests and case expectations are never included in either
agent prompt.

Both arms therefore receive one initial model call. ON may make additional
diagnostic-guided repair calls; initial and final correctness are reported
separately.

```bash
make benchmark-full-calibrate
TRIALS=1 MODEL=claude-sonnet-4-5-20250929 make benchmark-full-agent
```

The nine cases cover behavior traps, multi-file call sites and compatibility,
and verification/recovery failures. Results are written to
`eval/results/full-system-latest.json`.

See [../docs/13-full-system-benchmark.md](../docs/13-full-system-benchmark.md)
for the full experimental contract.

## Shared-patch verification ablation

This narrower mechanism test is a paired OFF-vs-ON ablation over ten multi-file
error-handling conversions. The harness sees `tests/gate`; the independent
grader injects `tests/oracle` only after the final patch has landed. An oracle
failure therefore cannot be repaired by reading or overfitting to held-out tests.

First validate all reference controls:

```bash
make benchmark       # 10 good + 40 bad controls; must report 50/50
```

Then run Sonnet 4.5 through Anthropic (`ANTHROPIC_API_KEY` in `.env`):

```bash
make benchmark-agent
TRIALS=1 MODEL=claude-sonnet-4-5-20250929 make benchmark-agent
INPUT_COST_PER_MTOK=3 OUTPUT_COST_PER_MTOK=15 MODEL=my-model make benchmark-agent
```

For Ollama, LM Studio, or vLLM, set `PROVIDER=openai`, `MODEL`, and `BASE_URL`.

For a two-task pilot, call the module directly:

```bash
eval/.venv/bin/python -m eval.harness_bench --provider openai \
  --task withdraw --task reserve --trials 1
```

Each pair shares the exact initial model proposal. OFF writes that patch raw.
ON routes it through atomic parse, lint, type, and visible-test gates, then gives
gate feedback for at most two retries before `skipped-needs-human`. Results are
written to `eval/results/harness-latest.json` and include raw patches, token/time
usage, regressions shipped, catch rate, false rejection, escalation, and a paired
bootstrap confidence interval. Reference-control runs are plumbing checks and
must not be presented as model performance.

No `make`? Call the script directly: `bash eval/run_eval.sh`.

## Layout

- `run_eval.sh` — one-command runner: venv + deps + fetch + driver. Idempotent.
- `run_eval.py` — eval driver (curated-repo eval + RefactorBench smoke check; external-slice adapter is stubbed until the harness exists).
- `harness_bench.py` — calibrated paired harness benchmark and model adapter.
- `harness_tasks.py` — ten controlled tasks plus reference-good/bad patches.
- `full_system_bench.py` — independent-agent full-system benchmark runner.
- `full_system_cases/` — nine hidden-oracle full-system case repositories.
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
