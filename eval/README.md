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

No `make`? Call the script directly: `bash eval/run_eval.sh`.

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
