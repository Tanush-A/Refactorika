# Benchmarks & Evaluation

How we measure whether Refactorika works, and on what. This complements [09-success-metrics-and-demo.md](09-success-metrics-and-demo.md): §09 defines *what good looks like*; this file defines *which datasets we run to show it* and *what each one can and cannot prove*.

## What we're actually measuring

Refactorika does four separable things. We test them across **two surfaces**: a curated demo repo (ours, with ground truth) and RefactorBench (real, unseen repos). No single source covers all four — the audit is only testable on the curated repo.

| Capability | Component | Metric | Eval source |
|---|---|---|---|
| Convention-detection accuracy | §5.1 Audit | dominant-variant correctness, deviation precision/recall | **Curated repo only** (no public benchmark exists) |
| Call-site precision/recall | §5.2 Plan + §5.5 sweep | P/R vs ground-truth call-site set | **Curated repo** (primary) + RefactorBench (secondary) |
| Gate-pipeline correctness | §5.5 Verification harness | % committed edits passing all gates; planted-break catch rate; recover/escalate behavior | Curated repo + **RefactorBench slice** |
| Token efficiency | §5.4 Context layer | tokens vs realistic agent-loop baseline | Curated repo (multi-size = **[Reach]**) |

The headline framing stays correctness-first (caught violations + caught missed call sites), token savings second — see [05-core-components.md](05-core-components.md) §5.4.

**Coverage at a glance:**

| Component | Curated repo | RefactorBench |
|---|---|---|
| Audit (§5.1) | yes — only here | no |
| Call-site P/R (§5.2) | yes — ground truth | yes — via tests |
| Gate pipeline (§5.5) | yes | yes — unseen repos |
| Planted catch/recover/escalate | yes | partial |
| Token efficiency (§5.4) | yes | yes |

The story: **the curated repo proves the full pipeline including the audit; RefactorBench proves the execution + verification harness generalizes to real repos we didn't build.**

## The benchmark we run: RefactorBench

- **What:** 100 handcrafted **multi-file Python refactoring tasks** in popular open-source repos (Django, Salt, Flask, FastAPI, Celery, Ansible, Requests, Scrapy, Tornado); each task ships **3 natural-language instructions of varying specificity** and is mutually exclusive (tasks can be composed into longer ones on the same repo).
- **License:** Complex — the repo contains exact copies of the target OSS repositories, each under its original license (BSD, Apache 2.0, MIT, or GPLv3). The benchmark framework itself is not separately licensed; you consume the data under the constituent licenses of the included repos.
- **Why it fits:** solving a task requires *thorough cross-file dependency exploration and strict instruction adherence* — the exact "blast radius / missed call site" failure mode in [01-problem-and-purpose.md](01-problem-and-purpose.md). Tasks are test-verified, so they map directly onto our `verify_edit` pipeline.
- **Difficulty signal (from the paper):** baseline LM agents solve only **22%** of tasks on base instructions vs **87%** for a time-constrained human developer; a state-aware adaptation gave a **43.9%** relative improvement. So it's a hard, real benchmark — useful as a *credibility* signal, not an expected-to-ace target.
- **Use it for:** guided execution (§5.3) + verification harness (§5.5) + call-site sweep on *real, unseen* repos.
- **Caveat:** tasks are *prescribed* refactors (rename / extract / move), **not** "converge an inconsistent error-handling convention." It does **not** exercise our audit step (§5.1). The README recommends a Dockerized setup (SWE-agent's solution) for local running.
- **Source:** microsoft/RefactorBench (GitHub), arXiv:2503.07832 (ICLR 2025).

## Considered and dropped

- **SWE-bench (Verified / Lite)** — real test-gated Python GitHub issues; the famous agent leaderboard. **Dropped for v1:** its only unique role was "messy real Python repo at scale, test-gated," which **RefactorBench already provides** (Django, Flask, Celery, … real and unseen). It adds a heavy cost (Docker, ~120GB disk, 16GB RAM) for a leaderboard name-drop, not a capability we don't already cover. Revisit only if we want an external headline number. MIT-licensed; data on HuggingFace if reconsidered.
- **CanItEdit** — instructional **Python** code-editing benchmark (105 hand-crafted programs with before/after blocks, two instruction types: descriptive and lazy), BSD 3-Clause with ML restriction (cannot use as training data for ML models), code+data at `github.com/nuprl/CanItEdit` and HuggingFace (arXiv:2312.12450). Good for *edit-application quality* in isolation; small scale, single-file.
- **Long Code Arena** — repo-level Python tasks; its *module-summarization* task is relevant to our **context-file generation** (§5.6), and *CI-builds-repair* loosely mirrors the gate→recover loop.
- **RefactoringMiner + its oracle dataset** — AST-based refactoring *detection* with ground truth; conceptually close to our audit but **Java**, so not directly runnable on our Python pipeline.
- **CodeXGLUE code-refinement** — bug-fix pairs, mostly tiny / Java. Low value here; skip.

## The gap (why the curated repo is still load-bearing)

There is **no public, labeled benchmark for error-handling convention consistency** (exception vs result-type vs sentinel) with a ground-truth call-site set. Therefore:

- **Convention-detection accuracy** and **call-site precision/recall** must come from a **curated repo with known ground truth** — exactly the choice already locked in [08-risks-and-scope.md](08-risks-and-scope.md) and [09-success-metrics-and-demo.md](09-success-metrics-and-demo.md). This is the *honest* source for any false-negative number, not a public benchmark and not Sentry.

## Recommended plan (hackathon-sized)

- **Headline / demo (must-have):** the **curated 10-15 file repo** with planted inconsistencies + a known call-site set → reports detection accuracy + call-site P/R + every-edit-passes-gates. (Already the §09 plan.)
- **External credibility (nice-to-have):** run the *execution + verification* loop on a **small RefactorBench slice** (e.g. 5-10 tasks) to show the gates hold on real, unseen repos — this is the direct defense against the generalization risk in [08-risks-and-scope.md](08-risks-and-scope.md).
- **Skip for v1:** RefactoringMiner (Java), CodeXGLUE.

## Eval harness — scope

Two evaluators, one shared report format.

### A. Curated-repo evaluator (primary)
- **Ground-truth format** committed alongside the demo repo, e.g. `eval/ground_truth.json`:
  ```json
  {
    "dominant_variant": "exception",
    "deviating_files": ["svc/payments.py", "svc/auth.py"],
    "callsites": {
      "svc/payments.py::charge": ["api/checkout.py:42", "jobs/retry.py:88"]
    },
    "planted": {
      "missed_callsite": "jobs/retry.py:88",
      "type_clean_behavior_break": "svc/auth.py::login",
      "unrecoverable_edit": "svc/payments.py::refund"
    }
  }
  ```
- **Computes:** dominant-variant correctness; deviation precision/recall; call-site precision/recall (predicted set vs `callsites`); gate-pass rate; whether each planted item was caught / recovered / escalated as expected.

### B. External-slice adapter (credibility)
- A thin adapter that takes a RefactorBench task, runs our guided-execution + `verify_edit` loop against the checked-out repo, and records the standard per-edit log (`{ checks, retries, status, diff }` from [05a-verification-harness.md](05a-verification-harness.md)).
- **Reports:** task pass/fail (their test harness) + our gate outcomes, so we can claim "ran on N unseen real-repo tasks; every committed edit passed all gates; M escalated to `skipped-needs-human` rather than force-committed."
- **Note:** for these tasks the **audit step is bypassed** (the target convention/instruction comes from the benchmark, not our §5.1 inference). Only the plan / execution / harness path is exercised.

### Shared
- Both evaluators emit the same summary record so the demo dashboard renders curated + external results side by side. Token-usage tracking (§5.4) is captured by the same run wrapper.

## Bringing benchmarks into the repo

**We do not vendor benchmark data into the repo.** It's fetched on demand into a gitignored dir:

- **RefactorBench** → cloned into `eval/external/refactorbench/` (gitignored) via `eval/fetch_benchmarks.sh`. Not committed because it bundles full copies of 9 OSS repos under mixed licenses — including **GPLv3** (Ansible) — which would pull GPL obligations onto Refactorika, plus ~53MB / 21k files of bloat.
- **Committed (ours):** the curated demo repo, `eval/ground_truth.json`, and the adapter/harness driver code.

| Asset | Committed? | Why |
|---|---|---|
| Curated demo repo + `ground_truth.json` | Yes | Ours; the primary eval source |
| Adapter / harness driver code | Yes | Ours |
| RefactorBench data | No (gitignored) | GPLv3 (Ansible) + other OSS copies; ~53MB |

Fetch with:
```bash
bash eval/fetch_benchmarks.sh   # RefactorBench → eval/external/
```
See [../eval/README.md](../eval/README.md). Pin `REFACTORBENCH_REF` to a commit SHA in the fetch script before relying on results for reproducibility.

Or just run the whole eval in one command: `make eval` (creates venv, installs deps, fetches RefactorBench, runs the driver).

## Open items to confirm before committing

- RefactorBench: confirmed license (complex — constituent OSS repo licenses), confirmed Docker recommendation. Still TBD: whether a sub-slice can run standalone without the full SWE-agent scaffold.
- Whether external-slice eval is **[Initial]** or **[Reach]** given the build-time budget in [08-risks-and-scope.md](08-risks-and-scope.md).
