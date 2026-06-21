# refactorika — Build Plan (v2)

Berkeley AI Hackathon. A standalone tool that refactors a codebase as a whole-program problem: it removes dead code, cuts line count, and restructures across files, using a code graph for understanding, deterministic engines for the edits, Redis as the shared memory that holds the graph and the agents' decisions, and verification to keep the repo working.

Two delivery surfaces over one core engine:
- **Standalone CLI (primary): `refactorika <dir>`** runs the full pipeline, prints diffs and a metrics report, applies with `--apply`. Works with no agent, no Claude Code, nothing external.
- **MCP server (secondary):** the same engine exposed so Claude Code can call it mid-session.

---

## Pitch framing (say this out loud)

Refactoring is a whole-program graph problem, not a per-file one. The interesting changes (rename, move, dedup, dead-code removal, decomposition) are about relationships between files, so the tool that does them well needs a model of those relationships. The LLM brings judgment, deterministic engines bring correctness at scale, the graph connects them, and verification proves nothing broke.

External proof this direction is right: a Salesforce team migrating a 7-year-old codebase hit exactly the failure you'd predict from per-file LLM translation (files that looked correct but deviated at runtime because meaning lived in cross-file dependencies), fixed it with a complete dependency graph and leaf-to-root ordering, and took a 2-year manual estimate down to 4 months. Same architecture, production stakes.

Framing discipline: this is **ordered, verified, structural refactoring with the human in the loop where it matters**, not fire-and-forget autonomy. The Salesforce team had the full graph, ordering, transformation rules, and review, and still shipped functional gaps that only hands-on testing caught. Anyone who has done a real migration knows autonomy is oversold. Claiming "ordered + verified + structural" makes you sound like you understand the problem. Claiming "fully autonomous" makes you sound like you haven't done it.

---

## Core principle: division of labor

- **Graph = the whole-program world model.** Nodes are files and symbols, edges are imports, calls, references, exports. Prerequisite for any correct cross-file edit.
- **LLM = judgment only.** What to refactor, naming, how to decompose, which duplicates are real. For cross-file work it emits a transform *spec* (parameters), never a hand-written diff.
- **Deterministic engine = correctness at scale.** Takes the spec and applies it reference-correctly across every file.
- **Verify = the gate.** Confirms behavior preserved. Tools are the arbiter, not a second LLM.
- **Redis = the shared brain.** Holds the code graph (drives order + targets), the agents' working memory and refactoring decisions (drive consistency), and the vector index (drives dedup + similar-refactor retrieval). The plan is computed *in* Redis, not cached after the fact.

And the agent rule from the multi-agent research: **parallelize reading, keep writing single-threaded.** Read actions parallelize cleanly; conflicting writes produce incoherent results.

---

## Scope

**In for the hackathon:**
- Code graph (reference + call + import/export) with leaf-to-root ordering.
- Agentic understand → plan → apply pipeline.
- Dead-code removal (tiered, verify-by-deletion, cascade).
- LOC reduction (mostly deterministic via ruff).
- Cross-file transforms via deterministic engines; rename-propagation is the centerpiece.
- Duplication detection (two-stage) and consolidation.
- God-function decomposition (LLM judgment).
- Lightweight verification gate (parse + ruff + mypy + tests, git-revert on break).
- Standalone CLI + MCP server.
- Redis as the shared brain: code graph, agent memory + refactoring-decision memory, and vector retrieval (dedup + similar-refactor exemplars).

**Deferred to v2 (do NOT build now):**
- Characterization-test / golden-master gate (the strong behavior-preservation proof).
- Determinism guarantees and formal verification.
- Multi-language support.
- Anything approaching full autonomy.

State the deferrals out loud in the demo so they read as a roadmap, not a gap.

---

## Architecture

### 1. The code graph (the substrate)

Build a reference graph for the repo: symbol definitions, references, imports, exports, call edges. For Python, get this from LibCST's full-repository fact providers (fully qualified names + call graph) or tree-sitter. Store adjacency in Redis alongside code-chunk embeddings.

Two traversals, same graph:
- **Leaf-to-root (apply order).** Topologically sort so leaves (constants, utility helpers with no dependencies) come first. Refactor those first; every higher layer is then built on already-verified code.
- **Root-to-leaf (impact analysis).** Reverse the edges to answer "if I touch X, who transitively depends on it and must be re-verified."

### 2. Agentic pipeline (understand → plan → apply)

```
  SCOUTS (parallel, read-only)        PLANNER (single)            REFACTOR (sequential, per node)        CHECKER (deterministic)
  ┌───────────────────────┐      ┌──────────────────────┐     ┌──────────────────────────────┐     ┌─────────────────────┐
  │ one agent per file:   │      │ merge graph          │     │ for each node in leaf→root:  │     │ parse  (ms)         │
  │ summarize, extract    │ ───▶ │ topo sort leaf→root  │ ──▶ │  feed node + ALREADY-        │ ──▶ │ ruff   (ms)         │
  │ defs/refs/imports,    │      │ pick targets/node    │     │  refactored deps as verified │     │ mypy / pytest       │
  │ flag smells, embed    │      │ emit ordered worklist│     │  context; emit transform spec│     │ git revert on red   │
  └───────────────────────┘      └──────────────────────┘     └──────────────────────────────┘     └─────────────────────┘
        parallel = safe              one planner =                one node at a time =                  tools are the
        (reading only)               no conflicting decisions     no conflicting writes                 arbiter, not an LLM
```

- **Scouts (parallel, read-only).** Fan out one agent per file/module: summarize it, extract its slice of the graph, note smells, embed its functions. Merge into the global graph + vector store. This is the legitimate swarm, safe because nothing writes.
- **Planner (single agent).** Reads the merged graph, topo-sorts leaf-to-root, selects refactor targets per node, emits an ordered worklist where each item is a transform spec. One planner so decisions don't conflict.
- **Refactor (sequential, per node).** Walk the worklist in dependency order. For each node, give the LLM the node **plus its already-refactored dependencies as verified context** read from Redis Agent Memory (the Salesforce trick: stable reference points instead of guessing), and write the resulting decision back so later nodes stay consistent. It either parameterizes a deterministic transform or, for a local restructure, emits a whole-function rewrite applied by AST-node replacement.
- **Checker (deterministic, not an agent).** Parse → ruff → mypy → tests, git-revert on failure. Optional LLM reviewer on top for taste only.

Orchestration can be a plain loop or LangGraph. For 36 hours a plain loop is more reliable; reach for a framework only if it saves real time.

Build order caution: the single-threaded core (graph + planner + sequential apply + checker) is the thing that has to work on stage. Add the parallel Scout layer as the differentiator only once the core runs end to end. Do not let the swarm eat your time and flake live.

### 3. Transform engines

- **LibCST (primary, Python).** Lossless CST that preserves formatting and comments, parses Python 3.0 to 3.14, ships a codemod framework with CLI, parallelization, and diff output, and exposes FQN + call-graph facts for cross-file correctness. This is your main engine.
- **rope.** Turnkey semantic rename, move, extract, inline, change-signature with real reference resolution. Use it where its operation is exactly what you need. Caveat: parses only up to 3.10 syntax, so keep the demo repo off bleeding-edge syntax if you lean on it.
- **ast-grep.** Fast declarative structural find-and-patch over tree-sitter, multi-language. Use for "this smell shape → that fix shape" bulk rewrites. It is syntactic, not semantic, so not for reference-correct renames.

The LLM never hand-edits files for cross-file changes. For local function refactors, since you already hold the LibCST tree, apply by **AST-node replacement** (swap the FunctionDef node and reprint), not text diffs.

---

## Capabilities

### LOC reduction (mostly deterministic, mostly free from ruff)

| Capability | How | LOC impact |
|---|---|---|
| Remove unused imports / vars | ruff F401/F841, autoflake | high, instant |
| Remove unreachable code | ruff, vulture | medium |
| Simplify verbose patterns (else-after-return, nested ifs, bool returns) | ruff flake8-simplify (SIM) | high |
| Collapse loops into comprehensions | ruff flake8-comprehensions (C4) | medium |
| Modernize syntax | ruff pyupgrade (UP) | medium |
| Remove unused functions/classes | vulture candidates + gate | medium |
| Extract duplicated blocks into a helper | LLM plan + deterministic apply | high |
| Decompose god functions | LLM, AST-node replacement | restructures |
| Concise rewrite of bloated logic | LLM | high |

Rule: if ruff can do it, do not reimplement it. Spend build time on the graph, the agentic loop, the deterministic-transform wiring, and the demo.

### Dead-code removal (tiered, no per-repo whitelist)

Detection is cheap and noisy; safe deletion is the product. A real scan found vulture reporting 59 false positives on a codebase with zero actual dead code, mostly framework magic. So do not trust the detector, gate it.

- **Tier 1, delete freely (AST-local, near-zero risk):** unused imports, unused locals, code unreachable after return/raise. ruff + autoflake.
- **Tier 2, delete only behind the gate:** unused module-level functions/classes/methods. vulture candidates above a confidence threshold, then delete-and-verify, revert on break.
- **Tier 3, flag, never auto-delete:** anything dynamic or public.

Structural skip-rules (general, no per-repo tuning):
- Never auto-delete **decorated** defs. One rule covers FastAPI routes, Pydantic validators, pytest fixtures.
- Never delete `__all__` exports, dunders, or annotated class-level fields (handles Pydantic model fields).
- Everything else: attempt deletion, let the check revert it.

**Cascade:** after a safe deletion, re-analyze. A removed function can orphan its helper, then a constant, then an import. Iterate to a fixpoint.

### Cross-file transforms (the deterministic centerpiece)

- **Rename-propagation (demo centerpiece).** Rename one symbol, update every call site, import, and re-export across files via rope/LibCST. Provably complete, which prompting is not.
- **Move symbol + fix imports**, **change signature + update callers**: engine supports these, show as talking points.
- **Duplication consolidation:** extract a shared helper, rewire every importer.

### Duplication detection (two-stage)

1. **Structural hash (precision, cheap):** normalize the AST of each significant node (strip comments/docstrings/literals, optionally alpha-rename locals), hash it. Catches type-1/type-2 clones exactly.
2. **Embeddings (recall):** embed whole functions/classes (docstrings/comments stripped, minimum node size enforced) with all-MiniLM-L6-v2 on CPU, vector search in Redis for type-3 near-clones. High similarity threshold.

Structural hash for "the same," embeddings for "basically the same."

### God-function decomposition

LLM judgment: split a large function into well-named pieces. Apply by AST-node replacement, gate it, revert if it breaks.

---

## Verification (lightweight for MVP)

Heavy gate (characterization tests) is deferred. MVP gate is hierarchical so the cheap checks fail fast:

1. **Parse** (milliseconds) — did it stay syntactically valid.
2. **ruff** (milliseconds) — lint/format clean.
3. **mypy** — types intact.
4. **pytest** — behavior intact.

**Apply/revert via git.** Each atomic transform is a commit; revert is `git reset`; you get the diff for the report for free, and "reverted on stage" becomes trivial.

**Timing (do not run the suite 15 times live).** Tests-still-green is your differentiator, so don't disable it. Run the cheap checks on every change, run the full suite once at the start (baseline green), once at the end (finale: "all N tests still pass"), and on the single change you stage to fail-and-revert. For per-change test gating without the pause, pytest-testmon selects only impacted tests, but on a small demo repo it is overkill.

---

## Diff-application reliability

Raw unified diffs from an LLM fail to apply (whitespace, context drift). Independently built agents converged on exact string replacement as more reliable. So:
- For local edits, prefer **AST-node replacement** (you hold the CST), or structured search/replace blocks as fallback.
- Build a **match-and-reflect** recovery loop: if a block does not match, show the model the closest actual lines and ask for a resend, bounded to a few retries. Reliability lives in the harness, not the format.
- Never apply raw unified diffs with `patch`.

This risk is contained anyway: cross-file edits go through the deterministic engines, so LLM text-edit risk only exists for local single-function refactors.

---

## Redis as the shared brain (core)

Redis is not a cache bolted on for the prize. It holds the state that figures out how to refactor, so the refactoring logic runs against Redis. Three load-bearing roles, mapped to Redis's Iris agent tooling (which the track names by product):

**1. The graph lives in Redis (decides order and targets).** Nodes (files/symbols) and edges (imports/calls/refs/exports) as native Redis structures: hashes for node metadata, sets for adjacency, sorted sets for ranking (importance, god-function size). The leaf-to-root topological order, target selection, and root-to-leaf impact analysis are all queries over this. The refactor *plan is a graph query in Redis*. Note: do not use the old RedisGraph module, it is deprecated; model adjacency with native structures, which is simpler for a hackathon anyway.

**2. Agent Memory as the blackboard + decision memory (drives consistency).** This is the strongest core use, and it matches Redis's own showcased coding-agent use of Agent Memory (storing engineering decisions and dev context across a coding agent's work). The parallel Scouts write findings to Redis; the Planner reads the merged state; the Refactor step reads each node's already-refactored dependencies from Redis (the leaf-to-root verified-context mechanism *is* a Redis read). And every completed refactor writes a decision back (pattern found, transform applied, name chosen) so later nodes refactor the same pattern the same way. That recall is literally "figuring out how to refactor": the tool stays consistent across the whole repo by remembering its own prior choices instead of re-deciding per file.

**3. Vector search for retrieval (drives dedup + consistency).** Embeddings of significant nodes in Redis Search power (a) duplicate detection and (b) "when refactoring node N, retrieve semantically similar already-refactored nodes as exemplars to mimic." That second use is context retrieval, exactly the track's language.

Optional if ahead: semantic caching of LLM refactor plans (Redis LangCache style) keyed by code embedding, so similar code reuses a plan. Saves tokens, speeds the demo, reinforces consistency.

Honest guardrails so this stays core-by-function, not core-by-volume:
- Redis is the brain and memory, not a substitute for transform correctness. LibCST/rope/ast-grep + the gate still do the actual safe edits. Do not let Redis features crowd out the engine.
- Skip the enterprise-data pieces of Iris (Context Retriever's business-entity modeling, the RDI/CDC data-sync layer). Those sync external databases; they have nothing to do with refactoring a local repo, and forcing them is the tacked-on move.
- Keep standalone working: ship a local Redis via docker-compose so `refactorika <dir>` runs self-contained, and point at Redis Cloud (25k credits) for the prize demo. "Standalone" means no agent needed, not no infrastructure.

Track fit: this hits "beyond caching" (graph + memory + vectors are live decision state, not a cache), "agent memory" and "context retrieval" (Iris's own framing), and "technical implementation / architecture" (Redis is structurally central). Name Agent Memory explicitly and note it is their showcased coding-agent pattern.

---

## Sponsor strategy

Tight set. Tacked-on integrations get penalized.

- **Primary — Anthropic.** Built on Claude Code, ships an MCP into it, LLM layer is Claude. Frame the meaningful problem as trustworthy maintenance of AI-generated code, the emerging bottleneck of AI-assisted software. Prize: $5000 API credits, office hour, SF visit.
- **Free fits (no extra work):** The Token Company (research depth: the smell taxonomy, the graph, the leaf-to-root design). Interaction Co (technical depth of integration + useful automation: the MCP-into-agent-workflow).
- **Core, not just an integration — Redis.** It is the shared brain (graph + agent memory + vectors), structurally central, which is exactly what their criteria reward. Lean on Agent Memory by name; it is Redis's own showcased coding-agent pattern. Prize: Mac Minis, 25k Redis Cloud credits.
- **Stretch only if ahead:** Arize (trace/eval the LLM transforms), Sentry (error monitoring). Skip if it would read as tacked on.

Do not chase Browserbase, Deepgram, Pika, Midjourney, QNX, Cognichip, Terac.

---

## Demo reliability

- **Run the deterministic transforms live.** They are reproducible, which is the selling point. Lean the live portion on what cannot surprise you.
- **Pin or pre-bake the LLM-judgment outputs** you will show (low temperature, or recorded). The LLM is the only nondeterministic part.
- **git revert is the money-shot mechanism.** Stage one change that fails the gate and watch it reset.
- **Framework false-positive as a feature.** Put one decorated-but-"unused" handler in the demo repo, let vulture flag it, show your tool refuse to delete it (or attempt-and-revert) and narrate "a raw linter would have gutted your API here." Do not whitelist it away; showcasing the catch beats hiding the problem.

---

## Phased build (~36h)

Split: one owner on engine (graph + cleanup + transforms + checker), one on agentic pipeline + MCP/CLI + demo. Reconverge for the refactor loop.

- **Phase 0 — Setup (1h).** Repo, one core package, pin tools, build/commit a deliberately sloppy demo repo (include a duplicated block, a god function, real dead code, and one decorated framework false-positive).
- **Phase 1 — Graph (4h).** Parse with LibCST, build the reference/call/import graph, store in Redis, topo-sort leaf-to-root. Lock the graph + findings JSON schema early; everything downstream reads it.
- **Phase 2 — Deterministic cleanup (2h).** Wire ruff --fix (SIM/C4/UP/F) + autoflake into the engine. Instant LOC reduction.
- **Phase 3 — Dead code (3h).** Tiered detection + skip-rules + delete-and-verify + cascade to fixpoint.
- **Phase 4 — Cross-file transform: rename-propagation (3h).** rope/LibCST rename across the repo. The centerpiece.
- **Phase 5 — Agentic loop + Redis memory (4h).** Planner (single) emits leaf-to-root worklist; Refactor agent processes per node, reading already-refactored deps and prior decisions from Redis Agent Memory and writing each new decision back for consistency; AST-node replacement for local rewrites; checker gates. Scouts (parallel, writing findings to Redis) added here if time.
- **Phase 6 — Verification + git apply/revert (2h).** Hierarchical gate, atomic commits, revert on break.
- **Phase 7 — Two front doors (3h).** Typer CLI `refactorika <dir>` (standalone, full pipeline, `--apply`) + FastMCP server over the same core. Confirm Claude Code can call it.
- **Phase 8 — Redis vector retrieval + report + demo polish (remaining).** Two-stage dup detection and similar-refactor retrieval over Redis Search, optional semantic plan cache, before/after metrics table, demo script, rehearse money shots. (Graph-in-Redis and Agent Memory already landed in Phases 1 and 5.)

---

## Demo script

1. Show the slop repo and baseline metrics (LOC, complexity, duplication, dead code; N tests green).
2. Run `refactorika <dir>`. It builds the graph, fans out scouts, plans leaf-to-root.
3. Watch deterministic cleanup strip dead imports/vars and collapse verbose patterns instantly.
4. **Rename-propagation:** rename one symbol, 12 call sites + imports across 5 files update correctly.
5. **Dead-code false positive caught:** the decorated handler gets flagged, the tool refuses/auto-reverts, "it didn't gut your API."
6. **Cascade:** a dead function removed, then its orphaned helper, then the import.
7. **Consistency via memory:** two near-duplicate blocks in different files get the same extraction and the same helper name, because the second refactor recalled the first from Redis Agent Memory. This is the Redis-as-brain moment.
8. **One staged change fails the gate and git-reverts on screen.** The proof.
9. Finale metrics: lines down X percent, complexity down, all N tests still green.
10. Switch to Claude Code, call the MCP mid-session, same cleanup inside the agent loop.

---

## Tech stack

Python target. LibCST (primary transforms + graph facts), rope (turnkey rename/move/extract), ast-grep (structural bulk rewrites), ruff + autoflake (deterministic cleanup), vulture (dead-code candidates), radon (complexity metrics), all-MiniLM-L6-v2 on CPU (dedup embeddings), Redis (the shared brain: graph + agent memory + vector search), FastMCP (MCP), Typer (CLI), Claude via the Anthropic API (judgment layer), git (atomic apply/revert).

---

## Deferred v2 roadmap (the moat, say it out loud)

Characterization-test / golden-master gate for hard behavior-preservation proof. Determinism guarantees and formal verification. Multi-language via ast-grep + tree-sitter. Higher autonomy as verification strengthens. These are why the project keeps going after the weekend.