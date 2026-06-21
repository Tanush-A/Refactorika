# CLAUDE.md — Refactorika (Hackathon Project Memory)

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
- **Advisory (read-only — finds + explains):** `analyze_file` · `find_duplicates` · `find_related` · `find_dead_code` · `generate_docs` · `get_context_map` · `audit_repo`/`get_plan`/`confirm_plan` (v3) · `get_log`. Surface ranked opportunities + memory; feed Claude's next proposal. `find_related` = impact check: hybrid-search the repo for semantically-similar code (+ call-graph dependents) before changing a file, so you don't fix one copy and miss the others.
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
- **Type gate:** `pyright` — reject only *new* type errors vs. pre-edit baseline (like lint; absolute "must be type-perfect" over-rejects correct code).
- **Lint/format gate:** `ruff` — normalize formatting, reject only *new* violations vs. pre-edit baseline.
- **Behavior gate:** `pytest` — type-clean ≠ behavior-preserving; catches silent regressions; *proves* dead-code/dup removals are safe.
- **Duplicate/dead-code analysis:** structural AST fingerprint (precise clones) **+** hybrid search — embeddings (`text-embedding-3-small` via OpenAI primary; `sentence-transformers` keyless fallback) fused with BM25 via Redis `FT.HYBRID`. Call-graph reachability for dead code.
- **Memory/state — Redis Iris via RedisVL (primary, JSON fallback):** four components — LangCache/AST-keyed cache · **Hybrid Search Index** (per-fn vector + BM25 + tags, `FT.HYBRID` RRF-fused — strictly better than pure cosine on code) · Agent Memory (cross-session context + refactor history) · Context Retriever (tag/num filters + hybrid retrieval). Hybrid needs Redis 8.4+ Query Engine — **as run: local Docker `redis:8` (8.8) on `:6380`, `--restart=always`** (Cloud/Stack also work); **degrades to brute-force vector / `.refactorika/` files** otherwise. Full detail: `docs/05-redis-iris.md`.

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
