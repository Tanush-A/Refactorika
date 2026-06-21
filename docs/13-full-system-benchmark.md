# Full-System Refactorika Benchmark

## Claim

Given an underspecified repository-level request, Refactorika should help an
agent land more correct, worthwhile refactors than the same model operating
without Refactorika, while shipping fewer regressions.

This benchmark is separate from `eval/harness_bench.py`, which isolates only
verification, rollback, and repair using a shared initial patch.

## Experimental contract

Each `(case, trial)` starts two isolated copies of the same baseline repository.
The recorded initial user prompt for both arms is exactly:

```text
refactor this codebase
```

The arms generate independent proposals:

- **OFF:** the model receives the generic request and visible repository
  snapshot, creates its own refactoring plan, then receives that plan and the
  repository in a second call to produce edits.
- **ON:** Refactorika runs its repository audit and dependency-ordered planning,
  reads repository architecture notes, and constructs a scoped edit prompt.
  The model produces edits independently from OFF. Refactorika applies parse,
  ruff, pyright, and visible pytest gates atomically. Rejections are rolled back
  and detailed gate results are returned for at most two repairs.

The intervention therefore includes Refactorika's context selection, prompt
construction, verification, rollback, and repair policy. Model, temperature,
repository, provider, and trial count are held constant. Token use and elapsed
time are reported rather than forced equal because efficiency is part of the
system-level tradeoff.

## Cases and grading

The nine controlled cases cover:

- behavior preservation across rounding, loop control flow, and near-duplicate
  semantics;
- multi-file renames, moves, call-site updates, re-exports, and compatibility;
- visible-test regression catches, type failures, and retry exhaustion.

Only baseline files are materialized for an agent. Oracle tests remain in the
evaluator and are injected after the final patch. Structural checks are also
held by the evaluator. A `correct_landed` result requires all of the following:

1. The agent produced an effective source edit.
2. The arm landed or shipped the edit according to its policy.
3. Visible and held-out behavior passes.
4. The case's machine-checkable structural target passes.

An edit that ships while failing held-out behavior is a `regression_shipped`.
An edit that preserves behavior but misses the structural target is an
`incomplete_refactor_shipped`. An ON proposal rejected after its repair budget
is `skipped-needs-human`.

## Commands

```bash
# Validate that baselines preserve behavior and still contain refactoring work.
make benchmark-full-calibrate

# Full run: nine cases x three trials x two arms.
MODEL=claude-sonnet-4-5-20250929 make benchmark-full-agent

# One-case pilot.
eval/.venv/bin/python -m eval.full_system_bench \
  --provider anthropic \
  --model claude-sonnet-4-5-20250929 \
  --case guard_clause_continue \
  --trials 1
```

Raw prompts, plans, patches, gate results, oracle outcomes, token counts, and
timings are written to `eval/results/full-system-latest.json` for auditability.

## Interpretation limits

These are project-owned controlled repositories. They test whether the system
handles specific failure modes; they do not establish general performance over
arbitrary production repositories. Run multiple trials and report raw counts,
not only percentages. Do not combine these results with the shared-patch
verification ablation because the interventions and experimental units differ.
