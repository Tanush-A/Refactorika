#!/usr/bin/env python3
"""RefactorBench evaluation driver for Refactorika.

Replaces the old audit-era driver. Runs the engine on RefactorBench tasks through the
verified spine and reports three honest numbers (in-scope pass rate, in-scope subtask
completion, out-of-scope count) — never a single "full 100" number. See refactorbench.py.

Usage:
    python eval/run_eval.py --smoke                 # 5 in-scope tasks, quick harness check
    python eval/run_eval.py --in-scope              # all in-scope tasks
    python eval/run_eval.py --in-scope --ablation   # in-scope, memory ON vs OFF
    python eval/run_eval.py --all                   # every task (scopes the rest honestly)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refactorbench as rb  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _print_summary(d: dict) -> None:
    print(f"\n=== Summary (level={d['level']}, memory={'on' if d['memory_on'] else 'off'}) ===")
    print(f"  in-scope pass rate:        {d['in_scope_pass_rate']:.1%} "
          f"({d['in_scope_passes']}/{d['totals']['in_scope']})")
    print(f"  in-scope subtask completion: {d['in_scope_subtask_completion']:.1%} "
          f"({d['subtests']['passed']}/{d['subtests']['total']})")
    print(f"  out-of-scope (declined):   {d['out_of_scope_count']}/{d['totals']['all_tasks']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="RefactorBench eval driver")
    ap.add_argument("--rb-dir", type=Path, default=rb.DEFAULT_RB_DIR)
    ap.add_argument("--level", default="base", choices=["base", "descriptive", "lazy"])
    ap.add_argument("--smoke", action="store_true", help="5 in-scope tasks (harness check)")
    ap.add_argument("--in-scope", action="store_true", help="all in-scope tasks")
    ap.add_argument("--all", action="store_true", help="every task (out-of-scope declined)")
    ap.add_argument("--ablation", action="store_true", help="run in-scope twice: memory ON vs OFF")
    ap.add_argument("--llm", action="store_true",
                    help="use the provider (Claude/Ollama) for NL->spec on unparsed tasks")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if not (args.rb_dir / "scripts").exists():
        print(f"RefactorBench not found at {args.rb_dir}. Run: bash eval/fetch_benchmarks.sh")
        return 1

    smoke = args.smoke
    only_in_scope = args.in_scope or args.ablation or smoke
    runs = [False, True] if args.ablation else [False]

    last = 0
    for memory_on in runs:
        print(f"\n>>> Running ({'memory ON' if memory_on else 'memory OFF'}) ...")
        summary = rb.run_eval(args.rb_dir, args.level, only_in_scope=only_in_scope,
                              smoke=smoke, limit=args.limit, memory_on=memory_on,
                              use_llm=args.llm)
        d = summary.to_dict()
        _print_summary(d)
        if args.llm:
            u = d["llm_usage"]
            print(f"  NL->spec model: {d['model']}  ·  LLM calls: {u['calls']}  ·  "
                  f"tokens: {u['input']} in / {u['output']} out")
        scope_tag = "inscope" if only_in_scope else "all"
        tag = f"{args.level}_{scope_tag}_mem{'on' if memory_on else 'off'}"
        rb.write_results(summary, RESULTS_DIR, tag)
        print(f"  written: eval/results/{tag}.json + .md")
        last = d["subtests"]["passed"]

    if args.ablation:
        print("\n=== Ablation: decision-memory ON vs OFF (subtask completion) ===")
        print("  Note: RefactorBench's in-scope subset is renames, where the target name is "
              "given by the instruction, so memory has limited room to change the outcome. "
              "Memory's benefit is on judgment tasks (naming choices), which this subset does "
              "not exercise — reported honestly rather than inflated.")
    return 0 if last >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
