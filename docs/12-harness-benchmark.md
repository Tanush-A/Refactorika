> **⚠ HISTORICAL — not maintained.** Preserved as a record of how the project evolved. For current docs see [docs/README.md](README.md).

# Harness Benchmark

## Claim under test

Routing an agent's multi-file refactor through Refactorika should increase the
number of correct changes that land and reduce behavior regressions that ship,
without rejecting a material fraction of correct changes.

## Experimental unit

One unit is `(task, trial)` and produces a paired result. The proposer is called
once against an isolated baseline repository. That exact initial patch is used
by both arms:

- **OFF:** write the patch directly, then grade it.
- **ON:** apply the patch atomically through parse, ruff, pyright, and visible
  pytest gates. A rejected patch is rolled back and its failure is returned to
  the proposer for at most two retries. Exhaustion produces
  `skipped-needs-human`, never a forced landing.

The intervention includes mandatory verification, rollback, and gate-guided
recovery. The paired initial patch prevents sampling differences from being
mistaken for harness value.

## Substrate and oracle

The benchmark contains ten Python tasks converting exception-based service
functions to explicit `Result` returns while updating callers. Tasks cover
boundaries, fallbacks, calculations, and missed multi-file call sites.

`tests/gate` is visible to the proposer and is run by the harness. The held-out
`tests/oracle` suite is stored outside the materialized repository and injected
only by the grader. Final correctness is the held-out oracle result, not a gate
label.

## Calibration

Before agent trials, 50 controls must pass:

- Ten reference-good patches must pass every required gate and the oracle.
- Forty labeled bad patches cover behavior regressions, missed callers,
  syntax/lint/type defects, and held-out-only boundary defects.
- Every bad patch must fail the oracle. A held-out-only defect is expected to
  pass visible gates; it demonstrates residual risk rather than a harness catch.

Any failed control marks the run `void` and suppresses agent results.

## Metrics

Headline metrics are `correct_landed_rate` and `regressions_shipped` by arm.
Diagnostics include catch rate for initially bad proposals, false rejection of
initially good proposals, escalation, retries, gate skips, tokens, wall time,
and a paired bootstrap 95% interval for the correct-landed rate delta.

Synthetic/reference controls are excluded from model-performance claims. A full
run uses ten tasks and at least three trials per task. Raw patches and per-run
records remain in the JSON artifact.

## Commands

```bash
make benchmark
MODEL=qwen2.5-coder:7b BASE_URL=http://localhost:11434/v1 make benchmark-agent
```

## Limitations

The task set is controlled and owned by this project. It validates the mechanism,
not broad repository generalization. RefactorBench remains a separate secondary
probe. Audit accuracy and call-site discovery precision/recall are separate
evaluations and must not be pooled into the harness safety result.
