# Benchmarks & Evaluation

How we measure Refactorika on real code — and how to read the numbers honestly. This replaces
the earlier audit-era eval plan.

## The benchmark: RefactorBench

[microsoft/RefactorBench](https://github.com/microsoft/RefactorBench) — 100 natural-language,
multi-file refactoring tasks across nine real OSS repos (Django, FastAPI, Celery, Scrapy,
Salt, Ansible, Requests, Flask, Tornado). Each task ships an instruction at three specificity
levels (base / descriptive / lazy) and an AST unit test that verifies the result. Baseline LM
agents solve ~22% on base instructions (per the paper) — it is a hard, real benchmark.

## How the adapter runs (eval/refactorbench.py)

Per task: **classify** the instruction against Refactorika's fixed transform menu; for an
in-scope task, set up an isolated copy of the repo, **map the instruction to a TransformSpec**,
apply it through the engine (rope rename, reference-correct, parse-gated), then run the task's
**own AST unit test** and record subtests-passed / total and task pass/fail.

- **Isolation:** a fresh filesystem copy per task. The tests are pure AST/file checks needing no
  repo install, so this gives the same isolation as the dockerized setup the benchmark
  recommends, without the overhead.
- **Verification:** the task's AST test is the authoritative check; the engine parse-gates each
  edit. The repos' own (huge) suites are not run per task — impractical at scale, and not what
  RefactorBench verifies against.

## Honest scoping — three numbers, never one

Refactorika has a *fixed* transform menu (rename, dead-code, decompose, cleanup; move / dedup
are deferred). The adapter tags each task and **declines out-of-scope tasks explicitly** rather
than hallucinating an edit. We therefore report three numbers, not a single "X/100":

1. **In-scope pass rate** — tasks fully passed ÷ in-scope tasks.
2. **In-scope subtask completion** — AST subtests passed ÷ subtests over in-scope tasks.
3. **Out-of-scope count** — tasks declined (no matching engine).

The in-scope set for v1 is **single-symbol renames** (including constants and underscore-
prefixing) — what the rope-backed rename engine handles reference-correctly.

## Results (base level, committed to `eval/results/`)

| Metric | Value |
|---|---|
| In-scope pass rate | **54.5%** (6 / 11) |
| In-scope subtask completion | **90.9%** (80 / 88) |
| Out-of-scope (declined) | **89 / 100** |

The remaining in-scope misses are honest engine limits: some tasks also ask for `.rst`/`.txt`
documentation references to be updated (rope only rewrites `.py`), and a couple touch multiple
definitions. Renames that the engine does attempt are reference-correct across the repo.

## Ablation — decision memory ON vs OFF

We run the in-scope subset twice (`--ablation`). On this subset the result is **identical**
(54.5% / 90.9% either way), reported honestly rather than inflated: RefactorBench's in-scope
tasks are renames where the **target name is given by the instruction**, so decision memory has
no room to change the outcome. Memory's benefit is on *judgment* tasks (choosing consistent
helper names during decomposition) — demonstrated on the curated demo, not exercised by this
subset. This mirrors RefactorBench's own state-awareness finding: memory helps where the agent
must *decide*, not where the decision is dictated.

## Running it

```bash
make fetch          # clone RefactorBench into eval/external/ (gitignored, ~53MB)
make eval-smoke     # 5 in-scope tasks — quick harness check
make eval-inscope   # all in-scope tasks
make eval-ablation  # in-scope, memory ON vs OFF
make eval-all       # every task (out-of-scope declined)
```

Results are written to `eval/results/<level>_<scope>_mem<on|off>.{json,md}` (committed — they're
our reported numbers). The fetched repos under `eval/external/` are **not** committed (mixed
licenses incl. GPLv3 Ansible, ~53MB).

## Provider-agnostic & reproducible

The NL→spec mapping is deterministic for the rename subset (no key needed for the in-scope run).
When an LLM is used for classification of ambiguous instructions, it runs through the
provider-agnostic harness (Claude or Ollama) with the record/replay cache, so a reported run is
reproducible. The model used is recorded with the results.

## Deferred / future

`descriptive` and `lazy` instruction levels; an LLM classifier to pull more tasks in-scope;
move / signature-change / dedup engines to expand the in-scope set; per-task token-cost
accounting when LLM classification is enabled. (The display-spec notes in `12-benchmark-display-spec.md`
are folded into this document.)
