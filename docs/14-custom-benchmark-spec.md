# 14 · Custom Harness Benchmark — Design Spec (for review)

> **Status:** DESIGN ONLY — nothing built yet. Review and approve/adjust before
> implementation. Supersedes RefactorBench (Phase 2) as the *headline* benchmark;
> RefactorBench stays as a secondary "real unseen repos / safety probe" with the
> caveats already documented (our scaffold is single-shot, not SWE-agent).

## 1. Why a custom benchmark

RefactorBench measures multi-file **agent capability** with a full agentic loop
(SWE-agent, ~46–58 actions/task). That is **not what our harness does** and not
our strength — our single-shot adapter scores ~0, which says nothing about the
harness. Worse, the RefactorBench paper (ICLR 2025, §5) gives direct evidence that
*strict per-edit linting "backfires"* on multi-file refactors that need temporary
broken states.

The harness's real, narrow claim is different and testable:

> **When an autonomous agent edits code, the harness catches behavior-breaking
> edits before they land — and does not block correct edits — net increasing the
> rate of correct refactors that actually land and eliminating silently-shipped
> regressions.**

This benchmark is built to **test that claim and be able to falsify it.**

## 2. Hypotheses (stated so they can fail)

- **H1 (value):** correct refactors landed is higher with the harness ON than OFF.
- **H2 (safety):** behavior-breaking edits shipped is ~0 with ON, > 0 with OFF.
- **H3 (no backfire):** the harness's **false-rejection rate** (blocking *correct*
  edits) is low. If H3 fails, the RefactorBench critique applies to us and we say so.

## 3. Metrics

**Headline (oracle-judged, independent test suite):**
- `correct_landed`  — OFF vs ON  (edit committed AND oracle passes).
- `behavior_breaks_shipped` — OFF vs ON (edit landed AND oracle fails on behavior).

**Diagnostics (the honesty metrics):**
- `catch_rate` = caught / (edits that break the oracle). Harness value.
- `false_rejection_rate` = wrongly-blocked / (edits that pass the oracle). Backfire check.
- `escalation_rate`, `retries_to_success`, `tokens/task`, `wall/task`, `cost`.
- Defect **severity** of what OFF shipped & ON caught: `syntax | lint | type | behavior`.
  (The harness's unique contribution is **behavior** breaks that lint+type miss.)

## 4. Construct validity — why this isn't a rigged puff piece

1. **Independent oracle:** grading is the repo's `pytest`, separate from the gate stack.
2. **Ground-truth tasks:** each task has a known-correct reference solution.
3. **Calibration controls** run first; if they fail, the run is void (§8).
4. **False-rejection is a first-class metric** — the benchmark can *disprove* H1/H3.
5. **Explicit caveat:** it is *our* repo and *our* tasks; results are a controlled
   demonstration of the mechanism, not a leaderboard claim.

## 5. Substrate repo (`eval/substrate_repos/store/`)

A small but realistic, **pure-logic** (no I/O, deterministic) Python library with a
thorough test suite. Pure logic = the oracle can detect subtle behavior breaks
reliably. Proposed ~3 modules, ~350–500 LOC, ~45–60 tests:

- `pricing.py` — tiered discounts, stacking coupons, tax, rounding rules, shipping
  bands. Lots of branches + boundary conditions (the trap-rich core).
- `inventory.py` — stock reservation, backorder, restock thresholds.
- `text.py` — slugify / normalize / parse "k:v;k:v" config strings (string edge cases).

Each module ships with edge-case-heavy tests (`tests/test_*.py`) that pin the exact
behavior, including the boundaries the refactor tasks are designed to trip.

## 6. Task set (~12 tasks, Fowler-style refactors)

Each task = `(name, target file(s), instruction, type, behavior trap, reference good
edit, ≥1 reference bad edit)`. Mix of single-file and small multi-file. Every task
has a **behavior trap** — a spot where a naive edit silently changes behavior (so
lint/type pass but tests fail), which is where the harness earns its keep.

| # | Type | Example task | Behavior trap |
|---|------|--------------|---------------|
| 1 | Flatten nesting | guard-clause `compute_total` | a `continue` that skips tax accumulation |
| 2 | Extract helper | pull out tier-discount logic | dropped `tier == "silver"` branch |
| 3 | Inline variable | inline `rounded` | rounding applied at wrong step |
| 4 | Decompose conditional | split coupon eligibility | flipped `>=` vs `>` boundary |
| 5 | Replace magic numbers | constants for tax/shipping bands | off-by-one band edge |
| 6 | Rename + call sites | `calc_tax` → `compute_tax` (multi-file) | a missed call site / re-export |
| 7 | Parameterize | encapsulate shipping args into a dataclass | default weight changed |
| 8 | Dedupe | merge near-duplicate price fns | the two differ on free-shipping rule |
| 9 | Loop → comprehension | sum eligible line items | changes empty-list behavior |
| 10 | Introduce default param | add `tax_inclusive=False` | base case not actually False |
| 11 | Replace temp with query | recompute subtotal | recompute drops a discount |
| 12 | Extract class | `Cart` from loose functions | shared mutable state aliasing |

(Final list tuned during the pilot, §9.)

## 7. Arms (same machinery as `eval/agent_bench.py`)

Same agent, prompt, model, temperature, seed — **only the apply path differs.**

- **OFF (no-harness):** propose once → write raw → oracle grades. First broken edit ships.
- **ON (harness):** propose → `apply_and_verify[_multi]` (atomic, full gate stack) →
  on rollback feed `failure_reason` back → re-propose up to `max_retries` →
  else **escalate** (`skipped-needs-human`, never force-commit). Then oracle grades.

Atomic multi-file commit (judge the *complete* change, not intermediate steps) is the
design choice that sidesteps the paper's "temporary broken state" critique; the
false-rejection metric verifies it actually holds.

## 8. Calibration controls (self-test before the agent runs)

- Each task's **reference good edit** → must pass gates **and** oracle
  (catches harness over-blocking = false rejection at the control level).
- Each task's **reference bad edit(s)** (one per severity where applicable) → must be
  caught by a gate **or** fail the oracle (catches harness under-blocking).
- Each **behavior trap** is validated at build time: inject the bad edit, confirm a
  specific test fails. If a trap isn't test-detectable, the task is fixed or dropped.

If any control fails, the run is marked **VOID** (harness/oracle is broken).

## 9. Model, trials, statistics

- **Model:** `claude-sonnet-4-5` via Anthropic (capable enough for non-zero solves).
  Optionally also the local 7B as a "weaker model → bigger harness lift" data point.
- **Non-determinism:** temp 0 is *not* deterministic → run **T = 3 trials/task**,
  report means + raw counts. ~12 tasks × 3 = 36 datapoints/arm.
- **Cost guard:** patch-based edits + per-run token/cost cap; live progress; crash-safe.

## 10. Reporting

New `report.py` section **"CUSTOM BENCHMARK (store, claude-…, N tasks × T trials)"**:
- Headline: correct landed OFF→ON; behavior-breaks shipped OFF→ON.
- Diagnostics: catch-rate, **false-rejection-rate**, escalation, retries, tokens, cost, wall.
- Severity breakdown of caught/shipped defects.
- A one-line **construct-validity caveat**.

## 11. Threats to validity (documented, not hidden)

- **Our repo/tasks** → construct validity. Mitigated by realistic pure-logic code, an
  independent oracle, calibration controls, and the false-rejection metric.
- **Difficulty calibration:** too easy → OFF already ~100% (no headroom); too hard →
  both ~0%. The pilot (§9) tunes tasks into the band where the agent gets a realistic
  mix of correct and subtly-broken edits.
- **Oracle completeness:** the harness can only help where breaks are test-detectable;
  we verify each trap is caught by a test (§8).
- **Non-determinism:** addressed via trials.

## 12. Build plan (after approval)

1. Author `store/` modules + edge-case tests.
2. Validate every behavior trap is test-detectable (inject bad edit → red).
3. Encode task specs (instruction + reference good/bad edits) in `eval/tasks_store.py`.
4. `eval/custom_bench.py` — reuse `agent_bench` arms; add false-rejection + severity.
5. Pilot 2 tasks × 1 trial to calibrate difficulty; tune.
6. Full run (Claude, 12 tasks × 3 trials), render report, write findings.

## 13. Open questions for review

- Domain OK (store/pricing), or prefer a different one (e.g. text/dates only)?
- Task count (12) and trials (3) acceptable for the API budget?
- Include the local 7B as a second model (weaker-model-bigger-lift story)?
- Keep RefactorBench in the suite as a secondary safety probe, or retire it?
