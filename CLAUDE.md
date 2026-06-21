# CLAUDE.md — Refactorika (Hackathon Project Memory)

> Master context every Claude Code session (and subagent) inherits. Keep it short, current, ruthlessly relevant.
> Stale context is worse than none. Update it as the project takes shape — cheapest, highest-leverage lever we have.

## What we're building
- **Product:** **Refactorika** — a convention-audit and guided-refactor layer for AI coding agents. (Formerly "Edit Memory"; some files/CLI/paths still use `editmemory` — the product name is Refactorika.)
- **It DOES:** Audits a Python repo for error-handling inconsistency, proposes the dominant convention (human confirms), plans a dependency-safe fix order, then **guides an agent through the refactor file-by-file with verification gates** (parse → lint → `pyright` → tests → call-site sweep) so **no edit lands in a broken state** — and persists the result as context files.
- **The core flip:** turns refactoring from an *unsupervised, repo-wide* agent task (high blast radius, no guardrails) into a *supervised, file-by-file* pipeline with automated gates at every step. The pitch is **"an agent did this work, but every step was checked"** — trust through verification, not through prompting.
- **The victim:** A dev/team with a legacy or AI-slop Python codebase (inconsistent error handling across files, written fast across many agent sessions) who wants the mechanical refactor done *safely*, not by hand and not by trusting an agent blind.
- **The 30-second magic moment:** Run audit on a deliberately inconsistent repo → confirm dominant pattern → watch files get fixed live → a planted type-clean-but-behavior-breaking edit gets **caught by the test gate, rolled back, and the agent recovers** via the re-propose loop → token chart shows a fraction of the baseline.
- **Shipped slice (the must-work demo):** Vertical slice on a 2-file repo, end-to-end: `audit → confirm → plan → check → verify → checkpoint/commit`. Broaden to 10–15 files only after the slice is green.
- **Sponsor tracks targeted:** **Redis Iris** [Initial — Agent Memory + Context Retriever + LangCache], **Fetch.ai / ASI:One** [orchestration delivery — see below], **Sentry** [Reach — AI agent monitoring].

## Source of truth files (read before acting — don't drift)
- `docs/` — **the master spec**, broken out by topic (start at `docs/README.md`). §references throughout this file map to these (e.g. §5.5 → `05a-verification-harness.md`):
  - `01-problem-and-purpose.md` · `02-target-user.md` · `03-tech-stack.md` · `04-architecture.md`
  - `05-core-components.md` + `05a-verification-harness.md` · `06-redis-integration.md` · `07-sentry-integration.md`
  - `08-risks-and-scope.md` · `09-success-metrics-and-demo.md`
- **Before parallel work starts:** freeze tool signatures + the per-edit JSON log schema (see Stack). That frozen interface *is* the contract — build to it, not into each other.

## Operating principles (hackathon — optimize for the demo)
- **Golden path first, always.** One repo, one convention, one flow, end-to-end. Green by the halfway mark, kept green. Land the 2-file vertical slice before breadth.
- **Make the action visible.** The whole product is "the agent did it but every step was checked" — so *render the checking*: live gate log, caught violation, rollback, re-propose, convergence re-audit. Invisible verification scores zero.
- **Fake what we can't build.** The demo repo is **curated**: deliberate, known inconsistencies; planted violations; a planted type-clean-but-test-breaking edit; explicit return-type annotations (tree-sitter sees syntax, not inferred types — see Risks). Ground truth is known so precision/recall is honest.
- **Reliability over code quality.** Fewer moving parts. Hardcoded fallback for every external call (Redis → local JSON; Fetch network → direct CLI/MCP; pyright/tests → skip-and-record, never silent-pass).
- **Stay in scope.** Honor the **[Initial]** vs **[Reach]** tags in the PRD. If it's not Initial, it's parked. New temptations → `## Parked`.
- **Small diffs, frequent commits.** The product itself checkpoints per task — we do too. `/checkpoint` after each working increment.

## Scope fences (the lines we do not cross in v1)
- **One convention type:** error-handling only (`exception` / `result-type` / `sentinel`, plus `mixed`/`ambiguous` labels). Not naming, not structure.
- **One language:** Python only.
- **Detection is tree-sitter-only** → only *syntactically visible* types: **explicitly-annotated** return types + recognized `Result`/`Maybe`/`Either` type names. Unannotated functions and cross-file alias resolution are **out** (honest blind spot, §10). `Awaitable[X]` / `async def` returns are unwrapped before classifying.
- **Call sites are best-effort, not IDE-grade:** direct `import` / `from … import` + direct `call` only. Dynamic dispatch, `__init__.py` re-exports, `getattr` string-keyed access, monkeypatching, cross-language = known false negatives, framed honestly. False-negative rate comes from the **ground-truth eval (§7)**, NOT from Sentry.
- **Sentinel caution:** `Optional[T]` / `T | None` is often a legit "not found," not an error — counted as `sentinel` only with a corroborating signal, else reported `ambiguous` and **not** a deviation (don't inflate the inconsistency number).

## Stack & conventions
- **Language:** Python (impl + analysis target). Fetch.ai wrapper layer is also **Python** (uAgents) — see Delivery.
- **Parsing/analysis:** `tree-sitter-python` (audit + pre-edit parse gate) · `pyright` (post-edit typecheck gate, single-file scope where possible) · `ruff` (lint + format gate) · `pytest` (behavioral gate) · AST symbol search + grep fallback (call-site detection).
- **Storage:** **Redis Cloud / Iris** primary; **local JSON** fallback/offline. Per-edit log schema (freeze this):
  `{ file, unit_id, variant_before, variant_after, checks: { parse, lint, typecheck, tests, callsite_sweep, handled_result }, retries, status, checkpoint_ref, diff }`
  where `status ∈ { committed, rolled-back, skipped-needs-human }`. **Skipped gates are recorded explicitly, never omitted** (honest coverage).
- **Run / lint / test:** `pytest` / `ruff` / `pyright` — pin the demo-repo `pyrightconfig.json` / `pyproject.toml` + a fast, deterministic `pytest` suite early (the gates depend on them).
- **Smoke test:** the vertical slice on the 2-file repo (`audit → confirm → plan → verify → checkpoint`).

## Delivery & integration layer
- **Primary: MCP server** (plugin for existing agent loops — Claude Code, Cursor). Tools/hooks:
  `run_audit · confirm_convention · get_plan · check_convention · get_impact · verify_edit · run_typecheck · run_lint · run_tests · checkpoint · record_edit`.
  `verify_edit` runs the full §5.5 gate pipeline; pre/post-edit hooks let the plugin gate the host agent's edits without it calling gates explicitly.
- **Fetch.ai / ASI:One orchestration** (sponsor delivery — verified feasible). ASI:One is a **hosted orchestrator LLM** (you don't deploy it; OpenAI-compatible API at `api.asi1.ai/v1`, `model="asi1"`); it discovers and calls a custom agent over the **Chat Protocol**. Refactorika **must run locally** (needs filesystem + `pyright`/`pytest` subprocesses) — Agentverse-hosted agents are sandboxed (no `subprocess`/`os`/`tree-sitter`). So:
  - Wrap as a **local uAgent + `mailbox=True`** (reachable behind NAT/firewall, auto-registers on the Almanac, discoverable by ASI:One). `publish_manifest=True` + a distinctive README/keywords for ranking; pin reliability by `@`-mentioning the agent address in demo.
  - **Reuse the MCP work** via the `uagents-adapter` MCP bridge: either `MCPServerAdapter` (FastMCP servers only) or an MCP-client-over-**stdio** uAgent that spawns the MCP server as a local subprocess (keeps filesystem access). The MCP server is the core; the uAgent is a thin wrapper, not a rewrite.
  - Long tasks: ASI:One uses a **poll** model (no push/callback) — build loading states. Run `pyright`/`pytest` via async subprocess so they don't block the agent event loop.
- **Fallback: CLI** — `editmemory audit <repo>`, `editmemory plan`, `editmemory check <diff>` — works against git diffs without a live agent loop.

## Sponsor integrations (don't change core scope)
- **Redis Iris [Initial]** — maps onto the existing mechanism, not bolted on:
  - **Agent Memory** → the rule list (long-term, *within-run* for Initial; cross-session is [Reach]) + session tier for the in-progress task list / execution log.
  - **Context Retriever** → backs `check_convention` / `get_impact` as typed, chainable structured lookups (exact match on the 3 variants — **not** vector search; vector is [Reach]).
  - **LangCache** → caches per-file classification calls, keyed on **normalized AST signature** (not semantic similarity — false hits would corrupt audit accuracy).
  - Demo: Redis Insight view of memory entries building up live; token chart splits LangCache LLM savings from structural (no-full-file-reload) savings.
  - **Risk:** provision Redis Cloud early — don't leave it to the last hours.
- **Sentry [Reach]** — per-tool spans on `check_convention` / `get_impact` / `record_edit` (error rate + latency), one end-to-end trace of audit→plan→execution, second source for token/cost. **Cannot** measure false negatives (no ground truth — that's §7's job). Lightest integration; first to descope to logs-only.

## The verification harness (§5.5) — the heart, gates run cheapest-first, short-circuit on fail
1. **Pre-edit (parse + variant)** — tree-sitter parse; reject if it won't parse or doesn't match the confirmed target variant.
2. **Lint/format** — `ruff check` + `ruff format --check` on touched files; reject *new* violations only (not pre-existing).
3. **Type check** — `pyright`; fail → roll back. No edit committed in a type-error state.
4. **Behavioral test gate** — run `pytest` covering touched files; type-checks ≠ behaves (a `raise`→`Result` conversion can regress silently). Roll back on fail; record a **skip** where no test covers the file.
5. **Call-site sweep + handled-result check** — re-scan recorded call sites: (a) none left in old convention, (b) callers actually *consume* the new convention (a returned `Result` is unwrapped, exceptions not silently dropped).
6. **Reject → re-propose loop** — bounded retries, surface failure reason to the agent.
7. **Escalation** — retries exhausted → mark `skipped-needs-human`, revert to last good state, flag in log, continue. **Never force-commit.**
8. **Per-edit audit log** — append the structured record (powers the demo dashboard).

**Workflow safety (§5.7), across edits:** per-task git checkpoint before next task · atomic multi-file units (`unit_id`: a file + its dependents commit together or all roll back) · blast-radius cap (reject edits touching files outside the planned set) · convergence re-audit at the end (adoption → ~100% on targeted files; remaining = `skipped-needs-human` or honest blind spots).

## Demo script (§8 — what the judge sees, in order)
1. Show the curated repo: deliberate inconsistent error handling (~10–15 files).
2. `audit` → report: dominant pattern + deviating files. 3. `plan` → ordered task list with call-site counts.
4. Guided execution: 3–4 files fixed; live catch of a violation + a **ground-truth-known** missed call site.
5. **Plant a type-clean but test-breaking edit** → test gate catches it after `pyright` passes → rollback to checkpoint → agent recovers.
6. **Plant an unrecoverable edit** → retries exhaust → `skipped-needs-human`, surfaced not force-committed.
7. Token-usage chart vs realistic agent-loop baseline. 8. Open a generated context file — accurate convention + dependents.
9. Convergence re-audit → ~100% adoption, blind spots reported honestly. (+ Redis Insight memory view, + Sentry trace if built.)

## Parallel-build rules — 4 devs, skeleton-first
> The dependency chain (audit→plan→execute) makes naive phasing serial. Beat it by **freezing the interface, then stubbing**: one dev ships a skeleton where the whole golden path runs on **mock data**, everyone else replaces stubs with real implementations against the frozen tool signatures + JSON log schema. The running mocked demo *is* the contract — no implicit shared state.
- **Hour 0 (all together, ~1h):** freeze the tool signatures + per-edit log schema above. Pin the demo-repo `pyrightconfig.json` / `pyproject.toml` + `pytest` command.
- **Then 4 parallel tracks** (file-editing agents use `isolation: worktree`; read-only exploration runs free):

| Dev | Owns | Notes |
|---|---|---|
| **1 — Skeleton/integration** | MCP server shell with **all tools stubbed (mock data)** so the full demo runs end-to-end day one · Fetch uAgent + ASI:One/Chat-Protocol wiring · CLI fallback · curated demo repo · dashboard | Critical path: don't move on until the mocked golden path runs. Everyone integrates *into* this. |
| **2 — Audit** | §5.1 tree-sitter classification (3 variants + mixed/ambiguous, async-unwrap) · `confirm_convention` · LangCache keying | Most open-ended → timebox hardest, descope first. |
| **3 — Plan + context** | §5.2 call-site detection (AST+grep) & safe ordering · §5.4 context-efficiency layer + token metric · §5.6 context files · Redis Context Retriever | |
| **4 — Verify + safety** | §5.5 harness (parse→lint→pyright→tests→sweep, re-propose, escalation) · §5.7 checkpoints/atomic units/blast-radius/re-audit · Redis Agent Memory · Sentry [Reach] | |

- Max 2–3 deeply-coupled tracks at once; the table above is independent by design (each builds to stubs).

## Build order (value-per-hour)
Vertical slice on 2 files first (guarantees a demoable artifact even if audit generalization lags). Within the harness, land gates in order: **parse + `pyright`** → behavioral **test gate** → **lint**. Workflow safety starts as plain per-task commits, grows into atomic units only if time allows. Redis can start as local JSON and swap in. Sentry is last / first-to-cut.

## Environment
- API keys in `.env` (never commit); `.env.example` lists what's needed: **Redis Cloud** URL/creds, **ASI:One** API key, **Sentry** DSN [Reach].
- Two Fetch sign-ups: Agentverse (mailbox) + ASI:One (LLM key). Keep the uAgent **seed phrase stable** (it fixes the agent address ASI:One references).
- `.worktreeinclude` copies env files into each worktree automatically.

## Parked (tempting, explicitly NOT now — [Reach] or future)
- Multiple convention types audited at once · multi-language support.
- Cross-session/repo-lifecycle persistent memory (Reach upgrade of the Redis long-term tier).
- Vector-search rule retrieval (only pays off with many convention types).
- Inferred / imported-type resolution via a full type-resolver (e.g. pyright's API) (would close the tree-sitter blind spot).
- Incorporating human review corrections as a second rule source.
- Sub-linear-scaling token claims across repo sizes (Reach measurement).
