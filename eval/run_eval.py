#!/usr/bin/env python3
"""Refactorika evaluation driver.

Invoked by run_eval.sh (which handles venv + dependency install + benchmark
fetch). Can also be run directly inside an environment that already has
eval/requirements.txt installed:

    python eval/run_eval.py --external-dir eval/external

What it does today:
  1. Curated-repo eval  -- runs if eval/ground_truth.json exists, else skips.
  2. RefactorBench plumbing smoke check -- confirms the fetched benchmark is
     present and its task/test/mapping files line up, so collaborators get a
     clear pass/fail before the harness adapter is wired in.

Hook for later: once the verification harness (verify_edit) exists, plug the
external-slice adapter into `run_external_slice()` -- see
docs/11-benchmarks-and-eval.md section "Eval harness -- scope".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EVAL_DIR = REPO_ROOT / "eval"
GROUND_TRUTH = EVAL_DIR / "ground_truth.json"
RESULTS_DIR = EVAL_DIR / "results"

# Ensure the gate tools (ruff/pyright/pytest) pinned in eval/.venv resolve via
# shutil.which even when this script is run without activating the venv
# (e.g. `make benchmark`).
_VENV_BIN = EVAL_DIR / ".venv" / "bin"
if _VENV_BIN.is_dir() and str(_VENV_BIN) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def run_curated_eval() -> bool | None:
    """Curated demo-repo evaluator. Returns None if not set up yet."""
    print("\n=== Curated-repo eval ===")
    if not GROUND_TRUTH.exists():
        print(f"  SKIP: {GROUND_TRUTH.relative_to(REPO_ROOT)} not found.")
        print("        Add it once the curated demo repo exists "
              "(see docs/11-benchmarks-and-eval.md).")
        return None

    data = json.loads(GROUND_TRUTH.read_text())
    required = {"dominant_variant", "deviating_files", "callsites", "planted"}
    missing = required - data.keys()
    if missing:
        print(f"  FAIL: ground_truth.json missing keys: {sorted(missing)}")
        return False

    # TODO(harness): run the audit + call-site detection against the curated
    # repo and compare to `data`. For now we validate the ground-truth schema
    # so collaborators get a green light that the file is well-formed.
    print(f"  ground_truth.json OK "
          f"({len(data['deviating_files'])} deviating files, "
          f"{len(data['callsites'])} call-site groups).")
    print("  NOTE: scoring is a stub until the audit/harness lands.")
    return True


def run_refactorbench_smoke(external_dir: Path) -> bool:
    """Confirm the fetched RefactorBench data is internally consistent."""
    print("\n=== RefactorBench plumbing smoke check ===")
    rb = external_dir / "refactorbench"
    if not rb.exists():
        print(f"  FAIL: {rb} not found. Run: bash eval/fetch_benchmarks.sh")
        return False

    problems = rb / "problems" / "base_problems"
    tests = rb / "tests"
    repositories = rb / "repositories"

    ok = True
    for name, path in [("problems", problems), ("tests", tests),
                       ("repositories", repositories)]:
        exists = path.is_dir()
        ok = ok and exists
        print(f"  [{_status(exists)}] {name}: {path.relative_to(external_dir)}")
        if not exists:
            continue

    if not ok:
        return False

    # Spot-check that at least one task file has a matching test file.
    task_files = list(problems.glob("*/*-task.txt"))
    repos = [p.name for p in repositories.iterdir() if p.is_dir()]
    test_files = list(tests.glob("*/*.py"))
    print(f"  tasks: {len(task_files)} | repos: {len(repos)} | "
          f"test files: {len(test_files)}")

    sample_ok = len(task_files) > 0 and len(repos) > 0 and len(test_files) > 0
    print(f"  [{_status(sample_ok)}] benchmark is non-empty and discoverable")
    return sample_ok


def run_external_slice(external_dir: Path) -> bool | None:
    """External-slice adapter: drive the verification harness over RefactorBench
    tasks and grade with their tests. Stubbed until verify_edit exists."""
    print("\n=== External-slice adapter ===")
    print("  SKIP: verification harness (verify_edit) not implemented yet.")
    print("        Wire this to the harness per docs/11-benchmarks-and-eval.md.")
    return None


def _persist(results: dict) -> Path:
    """Write results/<ts>.json and update results/latest.json. Returns the path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = results["meta"]["timestamp"].replace(":", "")
    out = RESULTS_DIR / f"{ts}.json"
    out.write_text(json.dumps(results, indent=2))
    (RESULTS_DIR / "latest.json").write_text(json.dumps(results, indent=2))
    return out


def _maybe_run_agent(substrate, args) -> dict | None:
    """Phase-1 agent arms (no-harness vs harness). Returns the agent block, or
    None if the local model endpoint is unreachable (report stays `pending`)."""
    from eval import agent_bench  # noqa: PLC0415
    from eval.proposers import LocalAgentProposer  # noqa: PLC0415

    proposer = LocalAgentProposer(model=args.agent_model, base_url=args.agent_endpoint)
    if not proposer.available():
        print(f"  NOTE: agent endpoint {args.agent_endpoint} unreachable — "
              "skipping Phase-1 (start your local model, e.g. `ollama serve`).")
        return None
    print(f"  running agent arms: model={proposer.id} "
          f"tasks={args.agent_tasks or 'all'} max_retries={args.agent_max_retries} "
          "(local model — this can take a few minutes)")
    return agent_bench.run_agent_benchmark(
        substrate,
        proposer=proposer,
        max_retries=args.agent_max_retries,
        task_limit=args.agent_tasks,
        price_per_mtok=args.agent_price,
    )


def _maybe_run_refactorbench(args) -> dict | None:
    """Phase-2 RefactorBench slice (real unseen OSS repos). Returns the block, or
    None if the chosen model/provider is unavailable."""
    from eval import refactorbench  # noqa: PLC0415
    from eval.proposers import make_proposer  # noqa: PLC0415

    if args.refactorbench_provider == "anthropic":
        proposer = make_proposer("anthropic", model=args.refactorbench_model)
        if not proposer.available():
            print("  NOTE: ANTHROPIC_API_KEY not found (.env) — skipping RefactorBench.")
            return None
    else:
        proposer = make_proposer("local", model=args.agent_model, base_url=args.agent_endpoint)
        if not proposer.available():
            print(f"  NOTE: agent endpoint {args.agent_endpoint} unreachable — "
                  "skipping RefactorBench.")
            return None
    return refactorbench.run_refactorbench(
        repo=args.refactorbench_repo,
        proposer=proposer,
        task_limit=args.refactorbench_tasks,
        max_retries=args.agent_max_retries,
    )


def run_benchmark_suite(args) -> bool:
    """Benchmark: synthetic proposer (Phase 0) + optional real-agent arms
    (Phase 1) over available substrates, with the full report + run-over-run diff."""
    print("\n=== Refactorika benchmark ===")
    from eval import benchmark, report  # noqa: PLC0415
    from eval.substrates import available_substrates  # noqa: PLC0415

    substrates = available_substrates()
    if not substrates:
        print("  SKIP: no substrates available (demo_repo not found).")
        return False

    # Previous run (for the diff) BEFORE we overwrite latest.json.
    latest = RESULTS_DIR / "latest.json"
    previous = json.loads(latest.read_text()) if latest.exists() else None

    ok = True
    for substrate in substrates:
        results = benchmark.run_benchmark(substrate, trials=args.trials, seed=args.seed)
        if args.agent:
            agent_block = _maybe_run_agent(substrate, args)
            if agent_block:
                results["agent"] = agent_block
        if args.refactorbench and substrate.name == "demo_repo":
            rb_block = _maybe_run_refactorbench(args)
            if rb_block:
                results["refactorbench"] = rb_block
        out = _persist(results)
        print()
        print(report.render(results, previous))
        print(f"\nsaved -> {out.relative_to(REPO_ROOT)}")
        cal = results["calibration"]
        if cal["passed"] != cal["total"]:
            ok = False  # a failed control means the harness/grader is broken
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Refactorika eval driver")
    parser.add_argument("--external-dir", type=Path,
                        default=EVAL_DIR / "external",
                        help="Directory holding fetched benchmark data.")
    parser.add_argument("--benchmark", action="store_true",
                        help="Run the benchmark harness + terminal report.")
    parser.add_argument("--trials", type=int, default=1,
                        help="Trials per (task x tier). Synthetic proposer is deterministic.")
    parser.add_argument("--seed", type=int, default=7, help="Run seed (recorded in meta).")
    parser.add_argument("--agent", action="store_true",
                        help="Also run the Phase-1 real-agent arms (no-harness vs harness) "
                             "against a local OpenAI-compatible model endpoint.")
    parser.add_argument("--agent-model", default="qwen2.5-coder:7b",
                        help="Model id for the agent proposer.")
    parser.add_argument("--agent-endpoint", default="http://localhost:11434/v1",
                        help="OpenAI-compatible base URL (Ollama/LM Studio/vLLM).")
    parser.add_argument("--agent-max-retries", type=int, default=2,
                        help="Max re-proposals after a harness rollback before escalating.")
    parser.add_argument("--agent-tasks", type=int, default=None,
                        help="Limit number of agent tasks (default: all).")
    parser.add_argument("--agent-price", type=float, default=0.0,
                        help="Price per million tokens for the cost line (local model = 0).")
    parser.add_argument("--refactorbench", action="store_true",
                        help="Also run a RefactorBench slice (real OSS repos) via the local agent.")
    parser.add_argument("--refactorbench-repo", default="flask_refactor",
                        help="Which RefactorBench repo to slice (e.g. flask_refactor).")
    parser.add_argument("--refactorbench-tasks", type=int, default=3,
                        help="Number of RefactorBench tasks (smallest-primary-file first).")
    parser.add_argument("--refactorbench-provider", default="anthropic",
                        choices=["anthropic", "local"],
                        help="Model provider for RefactorBench (default: anthropic).")
    parser.add_argument("--refactorbench-model", default="claude-sonnet-4-5-20250929",
                        help="Model id for the RefactorBench agent.")
    args = parser.parse_args()

    if args.benchmark or args.agent or args.refactorbench:
        return 0 if run_benchmark_suite(args) else 1

    results: dict[str, bool | None] = {
        "curated": run_curated_eval(),
        "refactorbench_smoke": run_refactorbench_smoke(args.external_dir),
        "external_slice": run_external_slice(args.external_dir),
    }

    print("\n=== Summary ===")
    failed = False
    for name, res in results.items():
        label = "SKIP" if res is None else _status(res)
        print(f"  {label:>4}  {name}")
        if res is False:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
