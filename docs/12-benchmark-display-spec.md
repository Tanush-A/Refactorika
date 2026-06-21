# Refactorika Benchmark — Display & Reporting Spec

A spec for how final benchmark results are presented (data-dense terminal report + per-run JSON + run-over-run diff), **plus an implementation map tying every piece to a concrete file in the repo** so it isn't a floating doc.

> Scope: **spec + map, no code committed yet.** Defines the *output contract* and *where each part lands*, so it stays stable while the harness changes underneath.

## Where this lives (repo home)

This plan currently floats in `~/.windsurf/plans/`. Its permanent home:

- **The spec/prose (this doc)** → fold into `docs/11-benchmarks-and-eval.md` as new `## Display & reporting` + `## Implementation map` sections. That file is already the canonical benchmark doc (the capability/coverage tables) — this extends it rather than competing with it.
- **The code** → `eval/` modules per the map below.
- Keep the `~/.windsurf/plans` copy as scratch until merged into `docs/11`.

## Implementation map (where each piece is built)

Existing files: `eval/benchmark.py` (framework skeleton), `eval/run_eval.py` (driver), `eval/results/` (output), `refactorika/core/{gates,apply,analyze,schema,storage}.py` (harness).

| Spec piece | Target file | New/modify | Depends on |
|---|---|---|---|
| Tier runner — `raw`/`lint_type`/`full` gate subsets | `eval/benchmark.py` | modify | `core/gates.py` (call gates directly; **not** `apply_and_verify`, which is all-or-nothing + commits) |
| Proposer iface + Synthetic/Deterministic | `eval/benchmark.py` | exists | — |
| Independent oracle Grader (repo tests) | `eval/benchmark.py` | exists | per-repo deps for RefactorBench |
| Substrates (demo_repo now; **git-initialized** copies) | `eval/substrates.py` (new) | new | `git`, `eval/external` |
| Aggregation → pillars + model matrix | `eval/benchmark.py` | extend | — |
| Terminal report renderer (§HEADLINE + §1-5) | `eval/report.py` (new) | new | aggregate output |
| Run-over-run diff (tracked KPIs, `▲▼=`) | `eval/report.py` | new | `eval/results/*.json` |
| Persistence (`results/<ts>.json`, `latest.json`) | `eval/run_eval.py` | modify | — |
| Driver hooks (`--benchmark`, flags) | `eval/run_eval.py` | modify | `eval/benchmark.py` |
| Swag 4c code-health deltas | `eval/metrics_health.py` (new) | new | `radon` + `core/analyze.py` |
| Swag 4a comprehension proxy | `eval/metrics_health.py` | new | tokenizer + `core/analyze.py` |
| Real-agent proposer + model adapters (§5 matrix) | `eval/proposers.py` (new) | new (later) | agent loop / model API |
| Downstream ROI tasks (4a real, 4b $) | `eval/downstream/` (new) | new (later) | agent loop |

### Build order (phased)

- **Phase 0 — runnable now, no agent:** tier runner, synthetic proposer + controls, oracle grader, aggregation, `report.py`, persistence + diff, 4c health, 4a proxy. Produces §1 Reliability, §2 Enhancement, §4c, §4a-proxy.
- **Phase 1 — agent on demo_repo:** `proposers.py` (ClaudeProposer) + the re-propose loop → §3 Autonomy, retries/escalation/tokens, 4a-real, 4b $.
- **Phase 2 — RefactorBench:** `substrates.py` + per-repo dependency provisioning (the real blocker) → 4d credibility + unseen-repo reliability.
- **Phase 3 — multi-model:** model adapters + matrix aggregation/report → §5.

### Bindings & assumptions (gaps the implementer must honor)

1. **Tiers bypass `apply_and_verify`** — it runs all gates + git-commits. Run gate subsets directly for `raw`/`lint_type`.
2. **`full` tier needs a git repo** per isolated copy (rollback/commit logic in `apply.py`).
3. **Retries / escalation / tokens come from the agent loop**, not the harness (`apply_and_verify` does one attempt).
4. **RefactorBench oracle = installing each repo's deps** to run its tests; until then those rows are `committed_unverified`.
5. **On demo_repo the oracle == the harness test gate** (same `pytest`) → `full` is partly circular there; true independence is RefactorBench only.
6. **Schema is self-contained in this doc** (the `eval/benchmark.py` skeleton may be rewritten when the harness changes).

## Design principles

- **Data over flash.** Plain terminal text, alignment-first, no colors required (ASCII status markers only).
- **One stable output contract.** The same layout serves today's synthetic/demo run and the eventual real-agent + RefactorBench run. Swapping proposer/substrate changes the *numbers*, never the *format*.
- **Every number is attributable.** Each run is stamped with `model`, `harness git sha`, `substrate`, `grader`, `trials`, `seed` — because the harness is in flux and numbers are meaningless without provenance.
- **Honest by construction.** Show the cost side (false-rejection, retries, escalations, `committed_unverified`) next to the wins. Never report catch-rate alone.

## The headline framing (what the whole report argues)

> *Same agent, with vs without the harness.* The product's value = the **delta** between the no-harness arm and the harness arm, measured by an **independent oracle** (repo tests), not by the harness itself.

## Terminal report layout (the deliverable)   → `eval/report.py`

Anchor mock of the *final* (real-agent) report. Today's synthetic/demo run renders the identical sections with smaller numbers.

```
REFACTORIKA BENCHMARK · 2026-06-21 04:48Z
model=claude-opus-4.8   harness=git@a1b2c3   trials=3/task  seed=7
substrate=refactorbench(9 repos, 50 tasks) + demo_repo   grader=repo-tests (independent)
──────────────────────────────────────────────────────────────────────────────

HEADLINE  — same agent, harness OFF vs ON
  correct refactors landed:        no-harness 61%   →   harness 96%   (+35 pts)
  silent behavior breaks shipped:  no-harness 14    →   harness  0
  cost of safety:                  +1.4 retries/task,  3% good edits rolled back

1 · RELIABILITY   (catches bad edits, keeps good ones)
  tier        catch   broken_landed (by severity)        false_rej   unverified
  raw           0%    38  syn 9 · lint 7 · type 8 · beh 14    —           42
  lint_type    63%    14  beh 14                              2%          18
  full        100%     0                                      3%           0
  ► linter→harness delta = 14 silent behavior breaks caught that nothing else saw

2 · ENHANCEMENT   (landed refactors actually improved the code)
  opportunities resolved:   1,204 → 286   (76% reduction)
  avg nesting depth:        4.1 → 2.3      avg function length: 44 → 21 lines
  files improved:           312 / 418 touched

3 · AUTONOMY & COST   (completed without a human)
  autonomous completion:    47 / 50  (94%)
  escalated needs-human:    3        force-committed: 0
  retries per success:      1.4      tokens/task: 18k ± 6k     wall: 22s/task

CALIBRATION   controls 12/12 passed   (harness self-check OK)

──────────────────────────────────────────────────────────────────────────────
Δ vs previous run  (harness git@9f8e7d · 2026-06-20)
  full catch_rate      100%   =    unchanged
  behavior breaks        0    ▼2   IMPROVED  (.pyc retry leak fixed)
  false_rejection       3%    ▲1   REGRESSED (+1 good edit rolled back)
  autonomous            94%   ▲6   IMPROVED
  tokens/task          18k    ▼2k  IMPROVED
saved → eval/results/2026-06-21T0448.json   (diffed against results/prev.json)
```

### Section-by-section intent

- **HEADLINE** — 3 lines, the only thing a skimmer needs. The with/without delta + the silent-break count + the honest cost.
- **1 Reliability** — the ablation table (`raw` / `lint_type` / `full`). The `lint_type → full` row delta is the core argument. `broken_landed` is **severity-segmented**, foregrounding `beh` (silent behavior). `false_rej` and `unverified` keep it honest.  *→ `eval/benchmark.py` (tier runner + aggregate), rendered by `eval/report.py`.*
- **2 Enhancement** — before→after structural improvement of what *landed* (opportunities resolved, nesting, fn length).  *→ `eval/metrics_health.py` + `core/analyze.py`.*
- **3 Autonomy & Cost** — completion rate, escalations (and that force-commits stayed 0), retries, tokens, wall time.  *→ `eval/proposers.py` (agent loop emits retries/escalation/tokens); Phase 1.*
- **Calibration** — pass/fail of the negative controls; if a control fails, the harness or grader is itself broken (auto self-check).
- **Δ vs previous** — see below.

## Persistence + run-over-run diff   → persistence in `eval/run_eval.py`, diff in `eval/report.py`

- **Per run:** write full results to `eval/results/<ISO-timestamp>.json`; update `eval/results/latest.json`. (Dir gitignored except optionally a canonical `latest.json`.)
- **Diff:** compare current summary to the previous run's summary on a fixed set of **tracked KPIs**; render the `Δ vs previous` block. Each KPI declares a "good direction" so the report can label `IMPROVED` / `REGRESSED` / `unchanged` (ASCII `▲ ▼ =`, no color needed).
- **Attribution:** the diff header shows the *previous* run's `harness git sha` + date, so a regression is tied to a specific harness change.

### Tracked KPIs and their "good" direction

| KPI | Good direction |
|---|---|
| `full.catch_rate` | up |
| `full.broken_landed` (esp. `behavior`) | down |
| `false_rejection_rate` | down |
| `committed_unverified` | down |
| `opportunities_resolved_pct` | up |
| `autonomous_completion_rate` | up |
| `force_committed` | down (should stay 0) |
| `retries_per_success` | down |
| `tokens_per_task` | down |

## Data schema (machine-readable backing)   → emitted by `eval/benchmark.py`, written to `eval/results/`

Two artifacts per run; the terminal report is a pure render of these.

- **`results/<ts>.json`** — `{ meta, headline, reliability, enhancement, autonomy, calibration, tasks[] }`
  - `meta`: timestamp, model, harness_git_sha, substrate, grader, trials, seed, tool_versions.
  - `tasks[]`: one record per (task × trial) — `tier` outcomes, `severity`, `broken_landed`, `committed_unverified`, `structure_delta`, `retries`, `tokens`, `seconds`, `control_ok`. (Self-contained here; do not rely on the current `eval/benchmark.py` skeleton surviving the harness rewrite.)
- **`results/latest.json`** — pointer/copy of the most recent, used as the diff baseline.

(JSONL of `tasks[]` is optional if we later want external analysis; not required for the terminal flow.)

## How this scales without changing the display

| Stage | Proposer | Substrate | What changes | Display |
|---|---|---|---|---|
| Now | synthetic (labeled) | demo_repo | controls prove harness works | identical layout, small N |
| Next | real agent (Opus) | demo_repo | real edits, real retries/tokens | identical |
| Final | real agent | RefactorBench + demo_repo | scale + unseen repos | identical |

The report sections, KPIs, and diff are fixed now; only the rows fill in.

## 4 · "Swag" metrics — the value beyond catching bugs   → `eval/metrics_health.py` (+ `eval/proposers.py`, `eval/downstream/` for agent-dependent ones)

Reliability (§1) proves the harness is *safe*. These four prove it's *worth it*. All persist to the same JSON and render as terminal sections.

### 4a · Downstream Agent ROI — the hero metric (*refactoring pays for itself*)   → proxy: `eval/metrics_health.py` (now); real-agent: `eval/proposers.py` + `eval/downstream/` (Phase 1)

The argument: a refactored repo (smaller, flatter modules + generated context files) makes **future agentic coding cheaper and more reliable**. Measure the *same* follow-up task on the **messy** repo vs the **refactored** repo.

- **Real agent (headline, later):** run an agent on defined follow-up tasks (add feature / fix bug, test-verified) against each repo state. Track per task: `tokens_in/out`, `tool_calls/turns`, `wall_seconds`, `files_read`, `success` (tests pass).
- **Context-size proxy (runnable now, no agent):** estimate "tokens to comprehend a module" = file tokens + dependency fan-out (call-sites/imports to load) − context-file offset. Deterministic; validates *direction* before the agent exists.
- **Reported as:** before → after, with % reduction. Proxy and real-agent shown in the same block (proxy = direction, agent = magnitude).

```
4a · DOWNSTREAM AGENT ROI   (same task, messy repo vs refactored repo)
  ── real agent (3 follow-up tasks) ──────────────────────────────
  tokens/task:      82k  →  31k    (−62%)
  tool-calls/task:  41   →  18     (−56%)
  task success:     2/3  →  3/3
  ── context-size proxy (all modules) ───────────────────────────
  comprehension tokens/module (avg):  6.2k → 2.4k   (−61%)   [agrees w/ agent]
```

### 4b · Dollar-cost & payback (*money framing*)   → `eval/report.py` (from per-task tokens + `meta.models[].price`); Phase 1

Convert tokens → $ at model pricing. Makes ROI legible to non-engineers.

```
4b · COST
  refactor cost (one-time):     $0.34 / module
  downstream saving:            $0.21 / future agent task
  payback:                      pays for itself after ~2 tasks
```

### 4c · Code-health deltas (*the code got objectively better*)   → `eval/metrics_health.py` (`radon` + `core/analyze.py`); runnable now

Before/after on what landed: total LOC, avg + max **cyclomatic complexity**, max **nesting depth**, **longest function**, **duplicate blocks**, **dead code removed**, modules with a generated context file.

```
4c · CODE HEALTH (before → after)
  LOC:               12,940 → 11,210      max nesting:   6 → 3
  avg complexity:    8.1 → 4.2            longest fn:    210 → 64 lines
  duplicate blocks:  37 → 9              context files: 0 → 312
```

### 4d · RefactorBench vs published baseline (*external credibility*)   → `eval/substrates.py` + driver; Phase 2 (needs per-repo deps)

Our solve rate on a RefactorBench slice vs the paper's anchors (22% LM-agent, 87% human) — borrows the benchmark's authority.

```
4d · REFACTORBENCH (slice of N tasks)
  Refactorika solve rate:  XX%   |   paper LM-agent: 22%   human: 87%
  every committed edit passed all gates;  M escalated (force-committed: 0)
```

### Honesty rails (so swag stays credible)

- **Same agent/model both arms** in 4a — the only variable is repo state. Else it's not a fair ROI.
- **Proxy is labeled a proxy**; never report it as agent tokens.
- **$ uses a stated price + date**; pricing drifts.
- **Complexity tool named** (e.g. `radon`) so numbers are reproducible.
- 4a/4b/4d need the real-agent loop; until then they render `pending (needs agent loop)` rather than fake numbers. 4c + the 4a proxy are runnable now.

### New tracked KPIs (added to the diff table)

| KPI | Good direction |
|---|---|
| `downstream_tokens_per_task` | down |
| `downstream_toolcalls_per_task` | down |
| `downstream_task_success_rate` | up |
| `comprehension_tokens_per_module` (proxy) | down |
| `cost_payback_tasks` | down |
| `avg_complexity`, `max_nesting`, `longest_fn`, `duplicate_blocks` | down |
| `refactorbench_solve_rate` | up |

## 5 · Multi-model comparison (model × harness matrix)   → `eval/proposers.py` (adapters) + `eval/benchmark.py` (group by model×tier) + `eval/report.py` (matrix); Phase 3

**Yes, this is a natural fit** — a "model" is just a proposer configured with a `model_id`, so every run is defined by **(model × harness tier)**. Substrate, grader, tiers, and KPIs stay identical, making cells directly comparable. *Access mechanism deferred* (proposer stays model-agnostic; adapter chosen later).

### The matrix (the deliverable)

Rows = models, columns = tiers, each cell = the same KPIs. The headline is **cell-vs-cell**.

```
5 · MODEL × HARNESS   (substrate=demo_repo+refactorbench · trials=3 · seed=7)
                  raw (no harness)            full (harness)         harness lift
  opus-4.8        correct 88% · beh 4         correct 97% · beh 0      +9 pts
  kimi-k2         correct 71% · beh 11        correct 95% · beh 0     +24 pts
  ──────────────────────────────────────────────────────────────────────────
  ► kimi+harness (95%) ≈ opus-alone (88%)  at ~1/N the $/task   ← the swag
```

### The two stories it tells

- **Harness lift (vertical):** within one model, `full − raw` on correct-landed. Cheaper/smaller models get a *bigger* lift (more breaks to catch) → the harness matters most exactly where it's cheapest to run.
- **Cost-down substitution (diagonal):** does `cheap_model.full ≥ frontier_model.raw`? If yes, report **parity achieved** + the **cost ratio** ($/task of the cheap+harness combo vs the frontier-alone combo). This is your Kimi-vs-Opus hypothesis stated as a metric.

### Derived KPIs

| KPI | Meaning |
|---|---|
| `harness_lift[model]` | `full.correct_landed − raw.correct_landed` |
| `parity_vs_frontier[model]` | `cheap.full.correct ≥ frontier.raw.correct` (bool) |
| `cost_ratio[model]` | $/task of `cheap.full` ÷ `frontier.raw` |
| `beh_shipped_delta[model]` | silent breaks shipped, `raw → full` |

### Fairness rails (so the comparison is honest)

- **Identical inputs every cell:** same task prompts, substrate, grader, `trials`, `seed`, temperature — only `model_id` and harness tier vary.
- **Per-model provenance:** `meta.models[] = {id, version, access, price_per_mtok, price_date}`; cost is computed from the model's *own* pricing, not a blended rate.
- **Same trial count per model** (don't average 1 Opus run against 5 Kimi runs).
- Needs the real-agent loop → renders `pending (needs agent loop)` until then; nothing fabricated.

### Display / persistence impact

- Terminal: the matrix table above + a one-line "parity" verdict.
- JSON: `tasks[]` records already carry per-task model/cost; add `meta.models[]`. Aggregation groups by `(model, tier)`.
- Diff: tracked per `(model, tier)` cell, so a harness change that helps Opus but hurts Kimi is visible.

## What will shift as the harness changes (call-outs)

- **Severity buckets** track the gate set; if gates are added/removed, the `broken_landed` columns follow `TIER_GATES`.
- **`committed_unverified`** will matter more on RefactorBench (many files lack covering tests → test gate skips). Keep it prominent — it's the honest ceiling on "0 broken."
- **The `.pyc` retry leak** will surface in the `behavior` bucket once the real agent does multi-retry on the same file; the controls + diff are designed to catch exactly that.

## Open questions for the user

1. Headline KPI: is **"correct refactors landed (no-harness vs harness)"** the right single hero number, or do you prefer **"silent behavior breaks shipped"** as the lead?
2. Diff baseline: always "previous run", or pin to a named **baseline run** (e.g. last committed harness version)?
3. Retention: keep all `results/*.json`, or cap to last N?
