# CLAUDE.md — Refactorika (Hackathon Project Memory)

> Master context every Claude Code session (and subagent) inherits. Keep it short, current, ruthlessly relevant.
> This is the summary; **`docs/` is the source of truth** — read it before acting. §refs point into `docs/`.

## What we're building
- **Product:** **Refactorika** — a convention-audit and guided-refactor layer for AI coding agents. Written in **Python**, targets **Python** codebases.
- **It DOES:** Audits a Python repo for error-handling inconsistency, proposes the dominant convention (human confirms), plans a dependency-safe fix order, then **guides an agent through the refactor file-by-file behind verification gates** (parse → `ruff` → `pyright` → `pytest` → call-site/handled-result sweep) so **no edit lands in a broken state** — and persists the result as context files.
- **The core flip:** turns refactoring from an *unsupervised, repo-wide* agent task into a *supervised, file-by-file* pipeline with automated gates at every step. The pitch is **"an agent did this work, but every step was checked"** — trust through verification, not through prompting.
- **The victim:** A dev/team with a legacy or AI-slop Python codebase (inconsistent error handling across files, written fast across many agent sessions) who wants the mechanical refactor done *safely*, not by hand and not by trusting an agent blind.
- **The 30-second magic moment:** Run audit on a deliberately inconsistent repo → confirm dominant pattern → watch files get fixed live → a planted type-clean-but-behavior-breaking edit gets **caught by the test gate, rolled back, and the agent recovers** via the re-propose loop → token chart shows a fraction of the baseline.
- **Shipped slice (the must-work demo):** Vertical slice on a 2-file repo, end-to-end: `audit → confirm → plan → check → verify → commit`. Broaden to 10–15 files only after the slice is green.
- **Sponsor tracks targeted:** **Redis Iris** [Initial — Agent Memory + Context Retriever + LangCache], **Sentry** [Reach — AI agent monitoring].

## Source of truth files (read before acting — don't drift)
- `docs/` — the master spec, by topic (start at `docs/README.md`). §refs here map to these:
  - `01-problem-and-purpose.md` · `02-target-user.md` · `03-tech-stack.md` · `04-architecture.md`
  - `05-core-components.md` + `05a-verification-harness.md` (§5.5) · `06-redis-integration.md` · `07-sentry-integration.md`
  - `08-risks-and-scope.md` · `09-success-metrics-and-demo.md` · `10-usage-and-user-journey.md`
- **Before parallel work starts:** freeze the MCP tool signatures + the per-edit JSON log schema (below). That frozen interface *is* the contract — build to it, not into each other.

## Operating principles (hackathon — optimize for the demo)
- **Golden path first, always.** One repo, one convention, one flow, end-to-end. Green by the halfway mark, kept green. Land the 2-file vertical slice before breadth.
- **Make the action visible.** The whole product is "the agent did it but every step was checked" — so *render the checking*: live gate log, caught violation, rollback, re-propose, `skipped-needs-human`. Invisible verification scores zero.
- **Fake what we can't build.** The demo repo is **curated**: deliberate, known inconsistencies; planted violations; a planted type-clean-but-test-breaking edit; **explicit return annotations** (tree-sitter sees syntax, not inferred types — see Scope fences). Ground truth is known so precision/recall is honest.
- **Reliability over code quality.** Fewer moving parts. Hardcoded fallback for every external call (Redis → local JSON; `pyright`/`pytest` → skip-and-record, never silent-pass).
- **Stay in scope.** Honor **[Initial]** vs **[Reach]** tags. If it's not Initial, it's parked. New temptations → `## Parked`.
- **Small diffs, frequent commits.** `/checkpoint` after each working increment.

## Scope fences (the lines we do not cross in v1)
- **One convention type:** error-handling only — variants `exception` / `result-type` / `sentinel`, plus `mixed` and `ambiguous` labels. Not naming, not structure.
- **One language:** Python only (Refactorika is itself written in Python).
- **Detection is tree-sitter-only** → only *syntactically visible* types: **explicitly-annotated** return types + recognized `Result`/`Maybe`/`Either` names. Unannotated returns and cross-file alias resolution are **out** (honest blind spot, §08). `Awaitable`/`Coroutine` is unwrapped before classifying.
- **Call sites are best-effort, not IDE-grade:** direct `import`/`from … import` + direct `call` only. Dynamic dispatch, `getattr`/string-keyed access, `__init__.py` re-exports, monkeypatching = known false negatives, framed honestly. False-negative rate comes from the **ground-truth eval (§09)**, NOT from Sentry.
- **Sentinel caution:** `Optional[T]` / `T | None` is often a legit "not found," not an error — counted as `sentinel` only with a corroborating signal, else reported `ambiguous` and **not** a deviation (don't inflate the inconsistency number).

## Stack & conventions
- **Language:** Python (tool + analysis target).
- **Parsing/analysis:** `tree-sitter-python` (audit + pre-edit parse gate) · `pyright` (post-edit typecheck gate) · `ruff` (`check` + `format --check` lint gate) · `pytest` (behavioral gate) · AST symbol search + grep fallback (call-site detection).
- **Storage:** **Redis Cloud / Iris** primary; **local JSON** fallback/offline. Per-edit log schema (freeze this):
  `{ file, variant_before, variant_after, checks: { parse, lint, typecheck, tests, callsite_sweep, handled_result }, retries, status, diff }`
  where `status ∈ { committed, rolled-back, skipped-needs-human }`. **Skipped gates are recorded explicitly, never omitted** (honest coverage).
- **Smoke test:** the vertical slice on the 2-file repo (`audit → confirm → plan → verify → commit`).

## Delivery & integration layer — two interfaces over one core (§10)
- **Primary: MCP server** (plugin for MCP-compatible agents — Claude Code, Cursor). The *agent* proposes edits; Refactorika verifies them. Tools:
  `run_audit · confirm_convention · get_plan · check_convention · get_impact · verify_edit · run_typecheck · run_lint · run_tests · record_edit`.
  `verify_edit` runs the full §5.5 pipeline; `run_typecheck`→`pyright`, `run_lint`→`ruff`, `run_tests`→`pytest`.
- **Fallback: CLI** — `refactorika audit <repo>` / `confirm` / `plan` / `run` / `context`, plus `refactorika check <diff>` for CI/pre-commit. Works against git diffs without a live agent. Here Refactorika proposes edits itself (its own model call) — the one piece of logic that differs from MCP.
- **Keep both cheap:** core logic lives in one interface-agnostic library; CLI and MCP are thin shells over it; storage is read/written by the core so both see the same state.

## Sponsor integrations (don't change core scope)
- **Redis Iris [Initial]** — maps onto the existing mechanism, not bolted on:
  - **Agent Memory** → the rule list (long-term, *within-run* for Initial; cross-session is [Reach]) + session tier for the task list / execution log.
  - **Context Retriever** → backs `check_convention` / `get_impact` as typed, chainable structured lookups (exact match on the variants — **not** vector search; vector is [Reach]).
  - **LangCache** → caches per-file classification calls, keyed on **normalized AST signature** (not semantic similarity — false hits would corrupt audit accuracy).
  - Demo: Redis Insight view of memory building up live; token chart splits LangCache LLM savings from structural (no-full-file-reload) savings. **Risk:** provision Redis Cloud early.
- **Sentry [Reach]** — per-tool spans on `check_convention` / `get_impact` / `record_edit` (error rate + latency), one end-to-end trace of audit→plan→execution, second source for token/cost. **Cannot** measure false negatives (no ground truth — that's §09's job). Lightest integration; first to descope to logs-only.

## The verification harness (§5.5) — gates run cheapest-first, short-circuit on fail
1. **Pre-edit (parse + variant)** — `tree-sitter-python` parse; reject if it won't parse or doesn't match the confirmed target variant.
2. **Lint/format** — `ruff check` + `ruff format --check` on touched files; reject *new* violations only (not pre-existing).
3. **Type check** — `pyright`; fail → roll back. No edit committed in a type-error state.
4. **Behavioral test gate** — `pytest` over tests covering touched files; type-checks ≠ behaves (a `raise`→`Result` conversion can regress silently). Roll back on fail; record a **skip** where no test covers the file.
5. **Call-site sweep + handled-result check** — re-scan recorded call sites: (a) none left in old convention, (b) callers actually *consume* the new convention (returned `Result` unwrapped, caught exception not silently dropped).
6. **Reject → re-propose loop** — bounded retries, surface failure reason to the agent.
7. **Escalation** — retries exhausted → mark `skipped-needs-human`, revert to last good state, flag in log, continue. **Never force-commit.**
8. **Per-edit audit log** — append the structured record (powers the demo dashboard).

## Demo script (§09 — what the judge sees, in order)
1. Curated repo: deliberate inconsistent error handling (~10–15 files). 2. `audit` → report. 3. `plan` → ordered task list + call-site counts.
4. Guided execution: 3–4 files fixed; live catch of a violation + a **ground-truth-known** missed call site.
5. **Plant a type-clean but test-breaking edit** → test gate catches it after `pyright` passes → rollback → agent recovers.
6. **Plant an unrecoverable edit** → retries exhaust → `skipped-needs-human`, surfaced not force-committed.
7. Token-usage chart vs realistic agent-loop baseline. 8. Open a generated context file — accurate convention + dependents. (+ Redis Insight memory view, + Sentry trace if built.)

## Parallel-build rules — 4 devs, skeleton-first
> The dependency chain (audit→plan→execute) makes naive phasing serial. Beat it: **freeze the interface, then stub.** One dev ships a skeleton where the whole golden path runs on **mock data**; everyone else replaces stubs with real implementations against the frozen tool signatures + JSON log schema. The running mocked demo *is* the contract.
- **Hour 0 (all together, ~1h):** freeze tool signatures + per-edit log schema. Pin the demo-repo `pyright` config + `pytest` command.
- **Then 4 parallel tracks** (file-editing agents use `isolation: worktree`; read-only exploration runs free):

| Dev | Owns |
|---|---|
| **1 — Skeleton/integration** | MCP server with **all tools stubbed (mock data)** so the full demo runs day one · CLI fallback · interface-agnostic core module (§10) · curated demo repo · dashboard. Critical path — don't move on until the mocked golden path runs. |
| **2 — Audit** | §5.1 `tree-sitter-python` classification (exception/result-type/sentinel + mixed/ambiguous, Awaitable-unwrap) · `confirm_convention` · LangCache keying. Most open-ended → timebox hardest, descope first. |
| **3 — Plan + context** | §5.2 call-site detection (AST+grep) & safe ordering · §5.4 context-efficiency layer + token metric · §5.6 context files · Redis Context Retriever. |
| **4 — Verify + sponsors** | §5.5 harness (parse→ruff→pyright→pytest→sweep, re-propose, escalation) · Redis Agent Memory · Sentry [Reach]. |

## Build order (value-per-hour)
Vertical slice on 2 files first. Within the harness, land gates in order: **parse + `pyright`** → behavioral **`pytest`** gate → **`ruff`** lint/format. Redis can start as local JSON and swap in. Sentry is last / first-to-cut.

## Environment
- API keys in `.env` (never commit); `.env.example` lists what's needed: **Redis Cloud** URL/creds, **Sentry** DSN [Reach], and a model API key for the CLI's edit-proposal step.
- `.worktreeinclude` copies env files into each worktree automatically.

## Parked (tempting, explicitly NOT now — [Reach] or future)
- Multiple convention types at once · multi-language support.
- Cross-session/repo-lifecycle persistent memory (Reach upgrade of the Redis long-term tier).
- Vector-search rule retrieval (only pays off with many convention types).
- Inferred / unannotated-type resolution via a full type-resolver (`pyright` as detection engine) — would close the tree-sitter blind spot.
- Incorporating human review corrections as a second rule source.
- Sub-linear-scaling token claims across repo sizes (Reach measurement).
