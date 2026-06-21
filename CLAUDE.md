# CLAUDE.md — Refactorika (Hackathon Project Memory)

<<<<<<< HEAD
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
=======
> Self-contained context every Claude Code session and subagent inherits. Everything needed to act is **here** —
> `docs/` adds detail but you should never need to read it to make a correct move. Keep this short, current, ruthlessly relevant.

## What we're building
- **Product:** **Refactorika** — an **agent harness delivered as an MCP server**. Claude is the reasoning agent; Refactorika gives it three things it can't get alone: structure-aware analysis, a verification gate stack that proves every mutation safe, and Redis Iris cross-session memory. Written in Python, targets Python.
- **One-liner:** *Make safe structural change as frictionless as running a linter — point at a codebase, state the intent, get clean, reorganized, **proven-safe** code back, plus living docs of why it looks that way.*
- **The problem it kills:** Python repos rot four ways — **bad organization** (god-files, scattered/dup imports, bloated call sites), **rising complexity** (long functions, deep nesting), **context/doc rot** (the *why* evaporates as people leave), and **duplicate/dead code** (the same logic five ways; functions nothing reaches). Linters say *what's wrong*, not *how to restructure*; chat AI suggests fixes but is disconnected from the filesystem and has **no memory**. Refactorika runs as an MCP tool, so Claude reads, analyzes, applies, verifies, and *remembers* — without leaving the conversation.
- **The trust angle:** a mutation must change *shape, not behavior*. Every edit — including duplicate merges and dead-code deletions — passes gates (parse → `ruff` → `pyright` → `pytest`) before commit. The pitch is **"the agent restructured it, but nothing landed unverified."**
- **The memory angle:** knowledge *compounds*. Redis Iris (AST cache · vector index · agent memory · context retriever) makes the second run smarter than the first and keeps the *why* alive across sessions. See `docs/05-redis-iris.md`.
- **Target user:** a dev with a small/medium/legacy/AI-slop Python project who wants mechanical cleanup done *safely* — not by hand, not by trusting an agent blind.

## Two tool classes (everything is one or the other)
- **Advisory (read-only — finds + explains):** `analyze_file` · `find_duplicates` · `find_dead_code` · `generate_docs` · `get_context_map` · `get_log`. Surface ranked opportunities + memory; feed Claude's next proposal.
- **Verified mutation (gated — single atomic entrypoint):** `apply_and_verify(path, new_content, refactor_kind)`. Every structural edit goes through it — `refactor_kind` includes `consolidate_duplicate` / `remove_dead_code`, so "find dead code" becomes "**safely remove** it, proven by your tests."

## The core flow (golden path — must always work)
`analyze → propose → apply → verify → commit`
1. **Analyze** a file/repo with an advisory tool (organization · complexity · duplicates · dead code · context).
2. **Propose** a concrete edit — Claude writes the new file contents.
3. **Apply** via `apply_and_verify` (the working tree is never left dirty).
4. **Verify** through the gate stack; roll back atomically on any failure.
5. **Commit** only verified edits; log the `EditRecord`; update agent memory.

## The 30-second magic moment (the demo)
Run Refactorika on a curated messy 1–2 file repo → watch a god-function get **split + nesting flattened live** → a planted behavior-breaking "clean-looking" edit gets **caught by the `pytest` gate after `pyright` passes, rolled back, and re-proposed** → final diff is smaller, flatter, type-clean, green. The whole product is *visible verification* — render the gate log, the catch, the rollback. Invisible checking scores zero.

## Shipped slice (the trust spine — keep it green)
Vertical slice on a **2-file curated repo**, end-to-end: `analyze → propose → apply_and_verify → commit/rollback`. This verified-refactor loop is **shipped** and is the foundation everything else hangs off — keep it green while broadening.

## What's IN scope — the fences we do not cross
Target: **small-to-medium Python codebases** — single-package or small multi-file/multi-package repos, structure shallow enough to reason about statically. The four capabilities ship as one harness, sequenced by Build order.
- **Organization (verified mutation):** split large files into modules · reorder + dedupe imports (stdlib → third-party → local) · extract helpers from bloated call sites.
- **Complexity (verified mutation):** break long functions into named units · flatten deep nesting (guard clauses) · replace repeated blocks with extracted parameterized functions.
- **Duplicate/dead code (advisory → verified mutation):** `find_duplicates` (structural fingerprint + semantic vector search) · `find_dead_code` (call-graph reachability + confidence). Never auto-delete — surface, then consolidate/remove through `apply_and_verify`.
- **Context/docs (advisory + memory):** `generate_docs` emits/self-updates `.refactorika/context/<module>.md` · persisted to Redis Iris agent memory so knowledge compounds across sessions.

## What's OUT — park it, don't drift
- Multi-language (JS/TS/Go/…) — **Python only**.
- Large-scale architectural rewrites (monolith → microservices).
- **Any mutation that alters runtime behavior or public API** — preserve behavior, full stop (the invariant; proven by `pytest`).
- Test generation / coverage work (we *run* your tests as the safety net; we don't write them).
- Dependency management / `pyproject.toml` edits.
- *(Exploratory, not now: large deep-hierarchy monorepos, framework-aware refactors for Django/FastAPI, more languages.)*

## Stack
- **Language:** Python 3.11+ (harness **and** target).
- **MCP:** `mcp` Python SDK (`FastMCP`) — exposes capabilities as tools Claude invokes inline.
- **Parse/analyze:** `tree-sitter` + `tree-sitter-python` — boundaries, import blocks, nesting depth, normalized AST fingerprints, the symbol graph for dead-code reachability.
- **Type gate:** `pyright` — refactored output must stay type-safe.
- **Lint/format gate:** `ruff` — normalize formatting, reject only *new* violations vs. pre-edit baseline.
- **Behavior gate:** `pytest` — type-clean ≠ behavior-preserving; catches silent regressions; *proves* dead-code/dup removals are safe.
- **Duplicate/dead-code analysis:** structural AST fingerprint (precise clones) **+** semantic embeddings — default `sentence-transformers` (local/offline, no key); optional `text-embedding-3-small` via OpenAI when `OPENAI_API_KEY` set. Call-graph reachability for dead code.
- **Memory/state — Redis Iris (primary, JSON fallback):** four components — LangCache/AST-keyed cache · Vector Index (`{file}:{fn}` embeddings) · Agent Memory (cross-session context + refactor history) · Context Retriever (structured + vector). **Always degrades to local `.refactorika/` files** so the demo runs offline. Full detail: `docs/05-redis-iris.md`.

## Architecture — one core, thin shells
- **Interface-agnostic core library** (`refactorika/core/` + `analysis/` + `memory/`) holds all logic: analysis, gate stack, transforms, Iris memory. Reads/writes state itself so every shell sees the same thing. Canonical package is top-level **`refactorika/`** — the old `src/refactorika/` skeleton is abandoned, do not add to it.
- **Primary shell: MCP server** (`refactorika/mcp_server.py`) — thin wrapper. **Advisory tools:** `analyze_file · find_duplicates · find_dead_code · generate_docs · get_context_map · get_log`. **Verified mutation:** `apply_and_verify(path, new_content, refactor_kind)`. Claude proposes/drives; Refactorika verifies + remembers. **Freeze tool signatures + the `EditRecord` schema before parallel work** — that frozen interface IS the contract.
- **Per-edit log schema (freeze this):**
  `{ file, refactor_kind, checks: { parse, lint, typecheck, tests }, retries, status, failure_reason, diff }`
  where `status ∈ { committed, rolled-back, skipped-needs-human }`. **Skipped gates recorded explicitly (`null`), never silently passed** (honest coverage).

## Verification gates — cheapest-first, short-circuit on fail
1. **Parse** — `tree-sitter-python` must parse the edited file; reject malformed edits before spending anything.
2. **Lint/format** — `ruff check` + `ruff format --check` on touched files; reject only *new* violations.
3. **Type** — `pyright`; fail → roll back. No edit committed in a type-error state.
4. **Behavior** — `pytest` over tests covering touched files. Type-clean ≠ correct. Roll back on fail; record a **skip** where no test covers the file (never silent-pass).
5. **Re-propose loop** — bounded retries; surface the failure reason back to the agent.
6. **Escalation** — retries exhausted → mark `skipped-needs-human`, revert to last good state, flag it, continue. **Never force-commit.**
7. **Log** — append the structured record (powers the demo dashboard).

## Operating principles (hackathon — optimize for the demo)
- **Golden path first, always.** One repo, one flow, end-to-end. Green by halfway, kept green. 2-file slice before breadth.
- **Make the action visible.** Render the checking — gate log, caught regression, rollback, re-propose, `skipped-needs-human`. The product *is* visible verification.
- **Fake what we can't build.** Demo repo is **curated**: known messy structure, a planted behavior-breaking edit, **explicit return annotations** (tree-sitter sees syntax, not inferred types). Ground truth known → honest before/after.
- **Reliability over code quality.** Fewer moving parts. Hardcoded fallback for every external call (Redis → local JSON; `pyright`/`pytest` unavailable → skip-and-record, never silent-pass).
- **Stay in scope.** Out-of-scope temptations go to `## Parked`, not into the build.
- **Small diffs, frequent commits.** Checkpoint after each working increment.

## Parallel-build (skeleton-first, beat the serial dependency chain)
**Hour 0 (all together):** freeze MCP tool signatures + per-edit log schema; pin the demo-repo `pyright` config + `pytest` command. Then one dev ships a **skeleton where the whole golden path runs on mock data** (the running mock IS the contract); everyone else replaces stubs with real impls against the frozen interface. File-editing agents use `isolation: worktree`; read-only exploration runs free.
- **Dev 1 — Skeleton/integration:** MCP server with all tools stubbed → full demo runs day one · core module · curated demo repo · dashboard. Critical path.
- **Dev 2 — Analysis:** structure detection (file size, import order/dupes, function length, nesting depth) · opportunity ranking · **duplicate detection** (structural fingerprint + semantic embeddings) · **call-graph reachability** for dead code.
- **Dev 3 — Transforms:** the actual edits (split / reorder / extract / flatten · consolidate-duplicate · remove-dead-code) · diff generation · **`generate_docs`** context emission.
- **Dev 4 — Verify + memory:** the gate stack (parse→ruff→pyright→pytest, re-propose, escalation) · **Redis Iris** (AST cache · vector index · agent memory · context retriever) + local-file fallback.

## Build order (value-per-hour)
1. **Verified-refactor loop** *(shipped)* — 2-file slice, one refactor kind end-to-end, gate stack green. Trust spine. (Gate landing order: **parse + `pyright`** → **`pytest`** → **`ruff`**. Redis started as JSON, now primary.)
2. **Duplicate detection** — highest demo impact; reuses tree-sitter AST. Add structural fingerprint + Redis vector index; consolidation rides the existing gate stack.
3. **Dead-code analysis + verified removal** — call-graph reachability; parallel to the embedding pipeline; removal rides the gate stack.
4. **Cross-session memory + living docs** — promote storage to full Redis Iris (agent memory + context retriever); `generate_docs` builds on retrievable prior context.

## Environment
- Keys in `.env` (never commit; gitignored); `.env.example` lists what's needed: `REDIS_URL` (primary, falls back to local JSON), optional `OPENAI_API_KEY` (embeddings — else local `sentence-transformers`). `.worktreeinclude` copies env files into each worktree.

## Parked (tempting, explicitly NOT now)
- Multi-language · architectural rewrites · behavior/API changes · test generation · dependency/`pyproject.toml` edits.
- Large deep-hierarchy monorepos · framework-aware (Django/FastAPI) refactors · per-team private embedding models.
>>>>>>> c96dee28d47b378d45255520cb4702fd3e74059a
