# CLAUDE.md — Refactorika (Hackathon Project Memory)

> Self-contained context every Claude Code session and subagent inherits. Everything needed to act is **here** —
> `docs/` adds detail but you should never need to read it to make a correct move. Keep this short, current, ruthlessly relevant.

## What we're building
- **Product:** **Refactorika** — an **MCP server** that gives Claude inline, verified structural-refactoring powers over a Python codebase. Written in Python, targets Python.
- **One-liner:** *Make structural refactoring as frictionless as running a linter — point at a codebase, state the intent, get clean reorganized code back, with every edit verified before it lands.*
- **The problem it kills:** Python repos rot two ways — **bad organization** (god-files, scattered/duplicate imports, bloated call sites) and **rising complexity** (long functions, deep nesting, tangled control flow). Linters say *what's wrong*; they don't fix *structure*. Chat AI suggests fixes but is disconnected from the filesystem — copy/paste hell. Refactorika runs as an MCP tool, so Claude reads files, analyzes structure, applies changes, and verifies the result **without leaving the conversation**.
- **The trust angle:** a refactor must change *shape, not behavior*. So every edit passes gates (parse → `ruff` → `pyright` → `pytest`) before commit. The pitch is **"the agent restructured it, but nothing landed unverified."**
- **Target user:** a dev with a small/legacy/AI-slop Python project who wants the mechanical cleanup done *safely*, not by hand and not by trusting an agent blind.

## The core flow (golden path — must always work)
`analyze → propose → apply → verify → commit`
1. **Analyze** a file/repo for refactor opportunities (organization + complexity).
2. **Propose** a concrete structural edit (split module, reorder imports, extract function, flatten conditional).
3. **Apply** the edit to the working tree.
4. **Verify** through the gate stack; roll back on any failure.
5. **Commit** only verified edits; log the result.

## The 30-second magic moment (the demo)
Run Refactorika on a curated messy 1–2 file repo → watch a god-function get **split + nesting flattened live** → a planted behavior-breaking "clean-looking" edit gets **caught by the `pytest` gate after `pyright` passes, rolled back, and re-proposed** → final diff is smaller, flatter, type-clean, green. The whole product is *visible verification* — render the gate log, the catch, the rollback. Invisible checking scores zero.

## Shipped slice (build this first, keep it green)
Vertical slice on a **2-file curated repo**, end-to-end: `analyze → propose → apply → verify → commit`. Broaden to more files / more refactor types only after the slice is green.

## What's IN scope (v1) — the fences we do not cross
Target: **simple Python codebases** — single-package or small multi-file scripts, shallow structure, self-contained logic.
- **Organization:** split large files into logically grouped modules · reorder + dedupe imports (stdlib → third-party → local) · extract reusable helpers from bloated call sites.
- **Complexity:** break long functions into smaller named units · flatten deep nesting (early returns / guard clauses) · replace repeated blocks with extracted parameterized functions.

## What's OUT (v1) — park it, don't drift
- Multi-language (JS/TS/Go/…) — **Python only**.
- Large-scale architectural rewrites (monolith → microservices).
- **Any change that alters runtime behavior or public API** — refactors preserve behavior, full stop.
- Test generation / coverage work.
- Dependency management / `pyproject.toml` edits.
- *(Future, not now: larger multi-package projects, framework-aware refactors for Django/FastAPI, more languages.)*

## Stack
- **Language:** Python 3.11+ (tool **and** target).
- **MCP:** `mcp` Python SDK — exposes refactoring as tools Claude invokes inline.
- **Parse/analyze:** `tree-sitter` + `tree-sitter-python` — function boundaries, import blocks, nesting depth, dup detection.
- **Type gate:** `pyright` — refactored output must stay type-safe.
- **Lint/format gate:** `ruff` (`check` + `format --check`) — normalize output, catch style regressions; reject *new* violations only.
- **Behavior gate:** `pytest` — type-clean ≠ behavior-preserving; this is what catches silent regressions.
- **State/cache:** **Redis** — cache analysis + refactoring results keyed on normalized AST signature, so re-seen files skip re-parsing. **Always have a local-JSON fallback** so the demo runs offline.

## Architecture — one core, thin shells
- **Interface-agnostic core library** holds all logic: analysis, proposal, gate stack, storage. Read/writes state itself so every shell sees the same thing.
- **Primary shell: MCP server** — thin wrapper exposing core as tools (e.g. `analyze_file · propose_refactor · apply_edit · verify_edit · run_typecheck · run_lint · run_tests · record_edit`). Claude proposes/drives; Refactorika verifies. **Freeze tool signatures + the per-edit log schema before any parallel work** — that frozen interface IS the contract.
- **Per-edit log schema (freeze this):**
  `{ file, refactor_kind, checks: { parse, lint, typecheck, tests }, retries, status, diff }`
  where `status ∈ { committed, rolled-back, skipped-needs-human }`. **Skipped gates are recorded explicitly, never silently passed** (honest coverage).

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
- **Dev 2 — Analysis:** `tree-sitter-python` structure detection (file size, import order/dupes, function length, nesting depth) · refactor-opportunity ranking.
- **Dev 3 — Refactor transforms:** the actual edits (split / reorder / extract / flatten) · diff generation.
- **Dev 4 — Verify + state:** the gate stack (parse→ruff→pyright→pytest, re-propose, escalation) · Redis cache + local-JSON fallback.

## Build order (value-per-hour)
2-file vertical slice first. Within the gate stack, land in order: **parse + `pyright`** → behavioral **`pytest`** → **`ruff`** lint/format. Redis starts as local JSON and swaps in. One refactor kind end-to-end before adding kinds.

## Environment
- Keys in `.env` (never commit); `.env.example` lists what's needed: Redis URL/creds, model API key. `.worktreeinclude` copies env files into each worktree.

## Parked (tempting, explicitly NOT now)
- Multi-language · architectural rewrites · behavior/API changes · test generation · dependency/`pyproject.toml` edits.
- Larger multi-package repos · framework-aware (Django/FastAPI) refactors · vector-search over analysis cache.
