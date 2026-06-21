# Evaluation & benchmarks

Three independent validations — **do not pool their results**. The four-arm full-system benchmark is
the headline. The benchmark code (`eval/agents/*`, `eval/full_system_bench.py`, cases,
`harness_bench.py`) is **identical on both branches**; RefactorBench (`eval/refactorbench.py`) is
**`main`-only**.

| Validation | Command | Question |
|---|---|---|
| Unit/integration tests | `make test` | Does the implementation behave as specified? See [testing.md](testing.md). |
| Full-system four-arm benchmark | `make benchmark-full-agent` | Can an agent discover + land a correct refactor from an underspecified request, and does the harness help? |
| Shared-patch ablation | `make benchmark-agent` | Given the *same* initial patch, does verify+rollback+repair improve the outcome? |
| RefactorBench [main] | `make eval-inscope` | How does the engine do on real OSS refactor tasks (honestly scoped)? |

---

## The four-arm full-system benchmark (headline)

The honest measurement: **the same agent, harness OFF vs ON**, graded by an **independent oracle**
(the case's own hidden tests), never by the harness itself. For every `(case, trial)`, each arm gets a
**fresh, isolated repo copy**; arms cannot affect each other. The user prompt is always exactly
`refactor this codebase`; hidden tests and structural targets are held by the evaluator and never
appear in the prompt, snapshot, harness context, or repair feedback.

### The four arms (internal name → display name)

| Display | Internal | Loop? | Harness? | What it does |
|---|---|---|---|---|
| **RAW** | `off` | no | no | One model call with the visible snapshot. Model returns whole changed files; patch written raw (no gates). |
| **HARNESS** | `on` | no | yes | One model call with Refactorika audit/plan context; patch routed through the atomic gate stack (parse→ruff→pyright→pytest), rolled back on failure, with up to `--max-retries` (default 2) diagnostic-guided repairs. |
| **AGENTIC RAW** | `agentic` | yes | no | Autonomous agent loop (`eval/agents/`) with bounded developer tools (list/read/search/run_tests/lint/typecheck/`submit_patch`). `submit_patch` is **ungated**. State machine: discover → select → plan → execute → verify → repair → completion-audit. |
| **AGENTIC HARNESS** | `agentic+harness` | yes | yes | Same loop, but a harness bootstrap preloads audit/plan/context and mutations go through `apply_and_verify` (gated). Enabled by `--agentic-mcp` (a historical flag name; it calls Refactorika's Python APIs in-process, not over MCP transport). |

Arms `off` and `on` are **independent proposals**, not the same patch — this measures the whole
workflow (discovery, context, verification, repair), not merely whether gates reject a known-bad edit.
`off`/`on` always run; the two loop arms are opt-in (`--agentic`, `--agentic-mcp`).

### Published headline numbers (the demo result)

From `docs/devpost.md` (matches `docs/bench4.png`), over the headline 45-case run:

| Arm | Success | Tokens | Time |
|---|---|---|---|
| RAW (`off`) | **71.1%** (32/45) | 37,000 | 190s |
| HARNESS (`on`) | **86.7%** (39/45) | 25,000 | 160s |
| AGENTIC RAW (`agentic`) | **75.6%** (34/45) | 1,374,925 | 1,655s |
| AGENTIC HARNESS (`agentic+harness`) | **83.3%** (≈37/45) | 805,600 | 1,208s |

Takeaways: the harness lifts a simple proposer **71.1% → 86.7%** while spending **fewer** tokens (37k→25k)
and **less** time (190s→160s) — safety that's also cheaper; and it lifts the full agentic loop
**75.6% → 83.3%** while cutting tokens **1.37M → 806k** and time **1,655s → 1,208s** (the verified spine
keeps the agent from thrashing). *(The full case registry is 49 cases — see below; the published
headline comparison was run over 45.)*

### The case suite (49 cases)

Each case is a small isolated repo with agent-visible source (and sometimes visible tests), at least
one intended behavior-preserving structural improvement, **hidden** pytest tests for semantic traps,
structural expectations, and expected/allowed paths for scope metrics. A result is `correct_landed`
only if the arm made an effective change, it landed, **and** visible+hidden behavior **and** the
structural target all pass — merely making tests pass is insufficient.

Categories (`eval/full_system_cases/`): foundational **behavior** (3), foundational **multi-file** (3),
foundational **recovery** (3), core **stress** (8), repository-**scale** (2 — 20-file & 100-file), plus
extra **semantic** (10), **contract/topology** (10), and **systems** (10) stress sets. (3+3+3+8+2+10+10+10
= 49.) The full per-case catalog with each case's trap is in the `working` `README.md` and
[14-benchmark-case-catalog-and-stress-plan.md](14-benchmark-case-catalog-and-stress-plan.md).

### Running it

```bash
make setup                      # once: build eval/.venv
make benchmark-full-calibrate   # validate every case baseline (no model calls) — must pass first

# single-case live pilot (cheap)
PATH="$PWD/eval/.venv/bin:$PATH" eval/.venv/bin/python -m eval.full_system_bench \
  --provider anthropic --model claude-sonnet-4-5-20250929 --case guard_clause_continue --trials 1

# full four-arm run
AGENTIC=1 AGENTIC_MCP=1 TRIALS=3 MODEL=claude-sonnet-4-5-20250929 make benchmark-full-agent
```

Key flags: `--agentic` / `--agentic-mcp` (enable the loop arms), `--parallel-arms` (run arms
concurrently, each in its own repo), `--max-retries` (ON repair budget, default 2), the timeout family
(`--request-timeout 180`, `--agent-timeout 900`, `--shell-timeout 30`, `--gate-timeout 180`,
`--agentic-max-iter 30`, `--agentic-mcp-max-iter 30`), cost reporting (`--input-cost-per-mtok` etc.),
and `--baseline <prior.json>` for a Sentry regression warning. Results default to
`eval/results/full-system-latest.json` (`--output` to change). Timeout/gate/infra failures mark a run
`invalid-infrastructure` (not a model failure). `eval/agents/` roles: `driver.py` (orchestration),
`loop.py` (state machine + budgets), `tools.py` (dev tools / `agentic`), `harness_tools.py` (harness
tools / `agentic+harness`), `providers.py` (HTTP model client), `prompts.py`, `schema.py`,
`metrics.py`, `campaign.py`.

---

## Shared-patch verification ablation (`eval/harness_bench.py`)

A narrower causal test: for each task **both arms share the exact same initial model patch** — OFF
writes it raw; ON routes it through parse/ruff/pyright/visible-pytest + rollback + up to two
diagnostic repairs; hidden oracle tests injected only for final grading. Ten tasks (`withdraw`,
`reserve`, `discount`, `parse_port`, `page_end`, `retry_delay`, `quota_left`, `shipping`, `score`,
`batch_count`) convert a function from raising `ValueError` to returning a result and updating its
caller. Calibrate 50 controls (1 good + 4 bad per task) first:

```bash
make benchmark                                              # calibrate (no model)
TRIALS=3 MODEL=claude-sonnet-4-5-20250929 make benchmark-agent
```

Results → `eval/results/harness-latest.json` (catch rate, false rejections, paired bootstrap CI).

---

## RefactorBench — [main] (`eval/refactorbench.py`)

The engine on [RefactorBench](https://github.com/microsoft/RefactorBench) (100 real multi-file tasks
across nine OSS repos, AST-verified). The adapter **classifies** each instruction against the fixed
transform menu, **declines out-of-scope tasks explicitly** (rather than hallucinate), applies in-scope
renames reference-correctly through the parse gate, and verifies with the task's own AST test. It
reports **three honest numbers**: in-scope pass rate, in-scope subtask completion, out-of-scope count.
Base results (`eval/results/`): **54.5% in-scope pass (6/11), 90.9% subtask completion (80/88), 89/100
declined**. The memory ON/OFF ablation is identical on this rename subset (targets are dictated by the
instruction). Run: `make eval-smoke | eval-inscope | eval-ablation | eval-all` (after `make fetch`).
