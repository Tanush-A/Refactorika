# Inspiration

According to Stripe's Developer Coefficient — a study conducted with Harris Poll across thousands of engineers and executives in 30+ industries — **42% of every developer's working week is spent on technical debt and bad code**. That's nearly $85 billion in lost productivity every year, not from building the wrong things, but from fighting the accumulated mess in codebases that already exist.

Every repo we've worked on has the same graveyard: god-files no one wants to touch, duplicate logic scattered across five modules, functions that were "temporary" two years ago. Linters tell you *what's wrong*, not *how to fix it*. AI chat suggests restructuring but is disconnected from the filesystem — it has no memory, no verification, and no way to know if the change it proposed just broke something in a file it hasn't seen.

We wanted a tool that could actually act: read the code, propose a structural change, apply it, *prove it's safe*, and remember what it did. Something that makes mechanical cleanup as frictionless as running a linter.

---

# What it does

Refactorika is an agent harness delivered as an MCP server. It plugs Claude directly into your codebase and gives it four capabilities every Python repo needs:

**Organization** — splits god-files into coherent modules, reorders and deduplicates imports (stdlib → third-party → local), extracts helpers from bloated call sites.

**Complexity reduction** — breaks long functions into named units, flattens deep nesting with guard clauses, replaces repeated inline blocks with parameterized helpers.

**Duplicate and dead code removal** — finds structural clones via AST fingerprinting and semantic near-clones via vector embeddings, then finds functions nothing reaches via call-graph reachability. Every removal goes through verification before it lands.

**Living documentation** — generates and self-updates `.refactorika/context/<module>.md` files that persist across sessions so the *why* doesn't evaporate when people leave.

The core constraint: **a mutation must change shape, not behavior.** Every edit — including duplicate merges and dead-code deletions — passes a cheapest-first gate stack before commit, and rolls back atomically on any failure.

**The demo moment:** A god-function gets split and its nesting flattened live. Then a planted behavior-breaking edit — one that looks clean and passes `pyright` — gets caught by the `pytest` gate, rolled back, and re-proposed with the failure reason surfaced back to the agent. The final diff is smaller, flatter, type-clean, and green. You watch the checking happen. That visibility *is the product.*

---

# How we built it

Refactorika is written entirely in Python 3.11+. The core design is:

```
Claude proposes a refactor
        ↓
Refactorika analyzes scope and dependencies
        ↓
Atomic file snapshot and mutation
        ↓
Parse → lint → typecheck → tests
        ↓
Commit on success / roll back on failure
        ↓
Persist result, context, and diagnostics
```

**Interface layer** — An MCP server (using Anthropic's MCP SDK) exposes all capabilities as tools Claude invokes inline. A companion CLI provides the same workflows in the terminal. A terminal dashboard renders gate results, committed edits, rollbacks, and human escalations in real time.

**Analysis** — `tree-sitter` + `tree-sitter-python` power all structural work: AST parsing, import extraction, function boundary detection, nesting depth, normalized structural fingerprints for duplicate detection, and a full call-graph for dead-code reachability.

**Verification gates** — Cheapest-first, short-circuit on failure:
1. **Parse** — tree-sitter must accept the edited file
2. **Lint/format** — `ruff check` + `ruff format --check`, new violations only vs. pre-edit baseline
3. **Type** — `pyright`, new errors only vs. pre-edit baseline
4. **Behavior** — `pytest` scoped to touched files; exit 5 (no covering tests) recorded as a skip, never a silent pass

Every gate outcome is tri-state — passed / failed / skipped — and recorded explicitly. Skipped checks are never treated as passes.

**Duplicate and dead code detection** — Structural AST fingerprinting catches exact clones. OpenAI `text-embedding-3-small` (with a `sentence-transformers` keyless local fallback) catches semantic near-clones. Redis `FT.HYBRID` fuses BM25 lexical scoring with vector similarity via Reciprocal Rank Fusion — strictly better than pure cosine on code. Dead-code reachability runs a custom call-graph analysis with explicit confidence levels (high / medium / low) based on how statically-reachable each symbol is.

**Memory — Redis Iris** — Four subsystems backed by Redis 8+: an AST-keyed analysis cache, a hybrid search index (vector + BM25 + tag filters), cross-session agent memory, and a context retriever. Falls back transparently to local `.refactorika/` JSON when Redis is unavailable — same interface, same behavior, no maintenance split.

**Atomic mutation** — Every edit, including multi-file changes, is treated as a single atomic unit. Original files are snapshotted before any write. Verification failure restores every file. Commits only land on full gate-stack success.

**Agent architecture** — Specialist agents (import, complexity, duplicate, dead-code) are orchestrated in dependency-ordered waves with concurrent execution via `ThreadPoolExecutor`. A confirmed plan is required before any agent runs.

---

# Challenges we ran into

**The verification paradox.** A change that looks clean to `pyright` can still silently break behavior. Getting `pytest` to run scoped to touched files reliably, handle missing coverage honestly, and still feel fast enough to be interactive took real iteration. The `skipped-needs-human` escalation status was a deliberate design decision: never force-commit, never pretend a gate passed when it didn't run.

**Duplicate detection at the right granularity.** Structural fingerprinting catches exact clones; semantic embeddings catch near-clones. Tuning the threshold so we surface actionable duplicates without drowning in false positives — e.g. getter/setter pairs that *should* look similar — required calibrating against real messy repos, not toy examples.

**Redis Iris offline fallback.** We wanted Redis as the primary memory layer but needed the demo to run anywhere, including on a laptop with no network. Building the fallback so it was genuinely transparent — same interface, same behavior, just slower and non-persistent — without creating a maintenance split was harder than it sounds.

**Tree-sitter sees syntax, not semantics.** Dead-code reachability works well for direct calls but misses dynamic dispatch, decorators that register functions implicitly, and `__all__` exports. We set explicit confidence levels and surface uncertainty rather than silently over-deleting.

---

# Accomplishments that we're proud of

We shipped a complete, end-to-end verified-refactor loop in a single hackathon: analyze → propose → apply → verify → commit or rollback. The trust spine is real — the gate stack catches regressions, rollback is atomic, and failure reasons route back to the agent for re-proposal.

We're also proud of the benchmark framework we built to measure whether Refactorika actually improves outcomes. Four arms — with and without the agentic loop, with and without the harness — run against 49 controlled cases spanning simple cleanup, multi-file renames, symbol moves, async cancellation, transaction rollback, and 100-file scale scenarios. Structural and behavioral correctness are graded separately, with hidden test targets the agent never sees.

---

# What we learned

The hardest part of a safety-first refactoring tool isn't the refactoring — it's defining what "safe" means precisely enough to enforce it mechanically. We learned to distinguish four failure modes that feel similar but need different responses: *malformed output* (parse gate), *style drift* (ruff gate), *type regression* (pyright gate), *behavior change* (pytest gate). Collapsing those into a single "verification failed" response would have made the tool useless for understanding *why* a proposed edit didn't land.

We also learned that **visible checking is the product.** An agent that silently refactors and hands you a diff you have to trust is just a faster way to introduce bugs. Rendering the gate log — especially the catch-and-rollback moment — is what transforms "AI did something to my code" into "I watched it get checked."

---

# What's next for Refactorika

**Broader duplicate consolidation** — the current flow surfaces duplicates and proposes a merge; we want to close the loop by automatically generating the unified implementation and running it through the full gate stack.

**Richer dead-code analysis** — handle dynamic dispatch, decorator-registered entry points, and `__all__`-exported symbols more precisely to push confidence levels up.

**Per-module context cards** — `generate_docs` already emits `.refactorika/context/` files; we want those to become queryable via `get_context_map` so Claude can answer "why does this module look this way?" with grounded, session-persistent memory.

**Multi-file refactors** — the gate stack today is per-file; coordinated moves across module boundaries (e.g. extracting a shared utility used in three files) require an atomic multi-file gate pass.

**Framework-aware analysis** — Django/FastAPI codebases have structural patterns (views, serializers, signals) that look like dead code to a naive call-graph but aren't. Awareness of those conventions would unlock a much broader safe-deletion surface.
