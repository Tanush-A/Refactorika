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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "eval"
GROUND_TRUTH = EVAL_DIR / "ground_truth.json"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Refactorika eval driver")
    parser.add_argument("--external-dir", type=Path,
                        default=EVAL_DIR / "external",
                        help="Directory holding fetched benchmark data.")
    args = parser.parse_args()

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
