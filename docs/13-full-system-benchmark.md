> **⚠ HISTORICAL — not maintained.** Preserved as a record of how the project evolved. For current docs see [docs/README.md](README.md).

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
  snapshot, then selects and implements a refactor in one autonomous call.
- **ON:** Refactorika runs its repository audit and dependency-ordered planning,
  reads repository architecture notes, and constructs a scoped edit prompt.
  The model produces edits independently from OFF. Refactorika applies parse,
  ruff, pyright, and visible pytest gates atomically. Rejections are rolled back
  and detailed gate results are returned for at most two repairs.

Both arms receive one initial model call. The intervention includes Refactorika's context selection, prompt
construction, verification, rollback, and repair policy. Model, temperature,
repository, provider, and trial count are held constant. Token use and elapsed
time are reported rather than forced equal because efficiency is part of the
system-level tradeoff.

## Cases and grading

The 49 controlled cases cover:

- behavior preservation across rounding, loop control flow, and near-duplicate
  semantics;
- multi-file renames, moves, call-site updates, re-exports, and compatibility;
- visible-test regression catches, type failures, and retry exhaustion.
- numeric boundaries, nested control flow, mutation, error chaining, generators,
  recursion, ordering, and cleanup;
- dependency topology, dynamic plugins, public signatures, dataclass/enum
  contracts, type protocols, generated files, and same-named decoys;
- async cancellation and ordering, transaction rollback, serialization,
  filesystem confinement, middleware order, caching, and resource safety.
- controlled 20- and 100-file repositories with identical relevant dependency
  clusters, deterministic distractors, and explicit size/LOC metadata.

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

## Metrics

Correctness is the headline: initial and final correct-landed rates, behavior
regressions, incomplete refactors, safe escalations, and paired wins/ties/losses.
Case-macro rates and case-clustered bootstrap intervals prevent repeated trials
from being treated as independent repositories.

Diagnostics cover model calls, input/output/cache tokens, explicit pricing,
audit/model/gate/grading/end-to-end time, required-path recall, unrelated-edit
precision, churn, missed call sites, compatibility, retries, and patch diversity.
The result schema is versioned under `meta.schema_version`.

## Error reporting

Set `SENTRY_DSN` to enable privacy-safe errors-only telemetry. Sentry receives
unexpected product, provider, grader, artifact, and baseline-comparison failures.
It never receives prompts, patches, source, local variables, raw diagnostics,
request bodies, or secrets. Normal model failures and gate rejections remain
only in the local JSON artifact.

Supplying `--baseline <artifact.json>` emits at most one sanitized warning when
ON correctness drops by more than `--regression-threshold` (default `0.10`) or
ON ships a behavior regression. Telemetry is fail-open and cannot invalidate a
run.

## Commands

```bash
# Validate that baselines preserve behavior and still contain refactoring work.
make benchmark-full-calibrate

# Full run: 49 cases x three trials x two arms.
MODEL=claude-sonnet-4-5-20250929 make benchmark-full-agent

# Optional cost accounting and aggregate regression warning.
INPUT_COST_PER_MTOK=3 OUTPUT_COST_PER_MTOK=15 \
BASELINE=eval/baselines/full-system.json make benchmark-full-agent

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

For a case-by-case map of the current suite and the proposed 120-case stress
catalog, see [14-benchmark-case-catalog-and-stress-plan.md](14-benchmark-case-catalog-and-stress-plan.md).
