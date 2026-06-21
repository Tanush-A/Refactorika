# Refactorika — Devpost

## Inspiration

According to Stripe's **Developer Coefficient** — a study with Harris Poll across thousands of engineers and C-suite executives in 30+ industries — **42% of every developer's working week** is spent dealing with technical debt and bad code. That's nearly **$85 billion** in lost productivity every year, not from building the wrong things, but from fighting the accumulated mess in codebases that already exist.

Every codebase has the same graveyard: god-files no one touches, duplicate logic scattered across five modules, functions that were "temporary." Linters tell you *what's* wrong but not *how* to fix it. Chat AI suggests restructuring but is disconnected from the filesystem, hallucinates edits that don't apply, and forgets everything the moment the session ends.

We kept hitting the same wall: **an LLM is great at deciding *what* to refactor and terrible at *doing* it safely.** Ask it to rename a function across a repo and it'll miss call sites, touch a same-named-but-unrelated symbol, or quietly change behavior. So we built the opposite of "let the AI rewrite your files." We split the job in two:

> **The LLM reasons about *what* and *how*. Real refactoring tools do the actual transformation, reference-correctly. A verification gate proves nothing broke — or reverts it.**

That's Refactorika.

## What it does

Refactorika is a **graph-driven, verified refactoring engine** for Python. Point it at a repo and it builds a reference-correct model of the whole program, plans a safe order of changes, applies them with deterministic refactoring tools, and **proves behavior is preserved** — committing each verified edit to git and rolling back anything that fails.

```bash
refactorika <dir>                 # dry-run: see the plan + every verified edit + before/after metrics
refactorika <dir> --apply         # write in place; each verified edit is its own git commit
refactorika <dir> --rename old=new # reference-correct cross-file rename
refactorika <dir> --llm           # add LLM judgment (decompose god functions, consistent naming)
refactorika <dir> --agents        # audit → dependency-ordered plan → specialist agents
```

It runs three ways over **one verified spine**: the **engine CLI**, an **MCP server** (drive it from Claude Code), and an **agent campaign**. The core promise is the same everywhere — *the engine restructured it, but nothing landed unverified.*

## The pipeline — the entire process, step by step

A run moves through six stages. Every stage has a single job, and the boundary between "AI judgment" and "deterministic execution" is hard.

**1. Build a reference-correct graph** (`graph/resolver.py`, **Jedi**).
We parse every `.py` file and use Jedi's real static name resolution to build a symbol graph: nodes are functions/classes/methods, edges are *true* references (`A → B` means A actually uses B, resolved through imports, aliases, and scopes — not a regex name match). This is the make-or-break: it's why a rename hits *every real reference and nothing that merely shares the name*. Entry points (public API, `__all__`, `__main__`, tests, route/fixture decorators) are flagged so we know what's reachable.

**2. Order the work** (`graph/order.py`, Tarjan SCC).
- **Leaf-to-root** topological order, so every refactor builds on already-verified code; cycles are reported, not guessed.
- **`impact_of(symbol)`** — reverse reachability gives the exact set of things a change can affect → we re-run **only the impacted tests**, not the whole suite, per edit.
- **`reachable_from(entry_points)`** — the complement is the dead-code candidate set.

**3. Plan — this is where the LLM reasons** (`pipeline/planner.py` + `planner_llm.py`).
Two planners produce the same contract (a `Worklist` of `TransformSpec`s):
- **Deterministic plan** (no LLM): remove private dead code (root-to-leaf), then per-module cleanup.
- **LLM plan** (judgment on top): it finds **god functions** via a *three-axis cohesion signal* — cyclomatic complexity ≥ 6 **or** length ≥ 30 lines **or** control-flow nesting ≥ 4 (`radon` + AST nesting), not a naive line count — and asks the model **how** to decompose them by responsibility. Crucially, **the LLM emits a `TransformSpec` (parameters), never a diff.** It decides *what* and *how*; it never writes the file.

**4. Execute with real refactoring tools** (`transforms/*`, pure functions that return an `EditMap`, never touch disk):

| What | Tool |
|---|---|
| Cross-file **rename** | **rope** (reference-correct, updates every call site/import) |
| **Cleanup** (unused imports, simplifications, format) | **autoflake** + **ruff** |
| **Dead-code removal** | **LibCST** (surgical node removal) |
| **Decompose / extract** a function | **LibCST** AST-node replacement |

These are battle-tested refactoring engines, not LLM text edits — so a cross-file rename is *provably complete* in a way prompting never is.

**5. Verify, then commit or revert** (`pipeline/checker.py` — the verified spine).
Every edit passes a cheapest-first, short-circuiting gate:
- **parse** (tree-sitter, before touching disk) →
- **lint** (`ruff`, rejects only *new* violations vs. a pre-edit baseline) →
- **type** (`pyright`, only *new* errors — so touching a file with pre-existing type noise doesn't spuriously fail) →
- **behavior** (`pytest`, scoped to the impacted tests).

All green → **`git commit`**. Any red or crash → **byte-for-byte restore**. Tools are the arbiter — *no LLM decides whether an edit is safe.* The full suite runs once at **baseline** (the repo must start green) and once at the **finale** ("all N tests still pass") as the authoritative backstop.

**6. Remember the decision** (`memory/decision_memory.py`, **Redis**).
Each refactoring decision (the pattern it acted on → the transform → the helper names chosen) is stored, indexed by an **embedding of the code**. Before the next similar function, the engine **recalls the most semantically similar prior decision** (exact structural match first, then vector similarity above a 0.86 cosine threshold) and **reuses the same names** — so the 2nd, 5th, Nth similar function is refactored *consistently*. This is Redis as live decision memory, not a cache.

The loop then repeats: rebuild the graph (positions shifted after the edit), take the next item, and cascade dead-code removal to a fixpoint.

## How we built it (tech stack)

**The program model & refactoring tools**
- **Jedi** — real static name binding; the reference-correct symbol graph (replaced a regex call-graph that mislinked same-named symbols).
- **rope** — reference-correct cross-file rename/move.
- **LibCST** — lossless AST-node replacement for decomposition + surgical dead-code removal (preserves formatting/comments).
- **tree-sitter / tree-sitter-python** — the parse gate, structural fingerprints (for decision-memory shape keys), and nesting depth.
- **ruff + autoflake** — deterministic cleanup and the lint gate.
- **radon** — complexity metrics + god-function detection.

**The verified spine**
- **pyright** — type gate (new errors only, vs. pre-edit baseline).
- **pytest** — behavior gate, **impact-scoped** (only tests reachable from the changed symbol); missing coverage recorded as a *skip*, never a silent pass.
- **git** — atomic commit per verified edit; rollback via byte-for-byte snapshot restore.

**The judgment layer (provider-agnostic)**
- Generation: **Anthropic Claude** or local **Ollama** — swappable by env. The LLM only ever returns structured `TransformSpec`s.
- Embeddings: **sentence-transformers** (local), **Ollama**, or **OpenAI** — *separate* from generation (Anthropic has no embeddings API, so the embedding backend must work regardless of the generation provider).
- A **record/replay cache keyed by (provider, model, prompt)** makes any run reproducible and lets a recorded run replay offline with no key.

**Memory & storage — Redis**
- **Redis** (redis-stack for RediSearch) is the live store for decision memory, the per-symbol **codebase vector index** (feeds the decompose prompt real neighbor context, `--show-similar`), the edit log, and the analysis cache.
- **Graceful fallback** to local `.refactorika/` JSON when Redis is unavailable — the engine never *depends* on Redis (or the LLM) being reachable.

**Front doors**
- **Typer** CLI (`refactorika <dir>`), **FastMCP** server (`claude mcp add refactorika -- python -m refactorika.mcp_server`), and an **agent campaign** (`--agents`: audit → dependency-ordered plan → specialist agents through the verified engine).

## Challenges we ran into

**The verification paradox.** A change that looks clean to the type checker can still silently break behavior. Getting `pytest` to run scoped to only the *impacted* tests (fast enough to feel interactive), and making the type/lint gates **baseline-aware** — rejecting only *new* errors so touching a file with pre-existing noise doesn't spuriously revert a good edit — took real iteration. The `skipped-needs-human` honesty (never force-commit, never pretend a gate passed that didn't run) was a deliberate design line.

**Refactoring should make code *less*, not more.** Our first LLM pass naively decomposed every function over a line threshold — and *added* hundreds of lines (more functions, more signatures). We learned that "decompose" is a structural trade, not a reduction, and rebuilt god-function detection around a three-axis cohesion signal so the LLM is *selective*, plus a bias toward reduction (dead code, dedup, cleanup) over restructuring.

**Reference-correctness on real repos.** rope crashes on intentionally-broken fixtures (Django ships syntax-error test files); the dead-code locator only found `def`/`class`, missing renamed constants. Real codebases are messier than toy examples — we hardened the tools (ignore-syntax-errors, constant location) against them.

**Redis as decision memory, not a cache.** We wanted Redis to *change the output* (consistent naming across files via semantic recall), not just speed it up — while keeping the demo runnable anywhere via a transparent JSON fallback with the same interface.

**Honest benchmarking.** Refactorika has a *fixed* transform menu, so on RefactorBench (100 real OSS tasks) we **decline out-of-scope tasks explicitly** rather than hallucinate, and report **three numbers** — in-scope pass rate, in-scope subtask completion, and out-of-scope count — never a single inflated figure.

## What we learned

The hardest part of a safety-first refactoring tool isn't the refactoring — it's **the division of labor**. The LLM is the right tool for *judgment* (what's worth changing, how to name the pieces) and the wrong tool for *execution* (it can't guarantee a rename is complete). Deterministic engines are the reverse. Put the model in charge of "what," put rope/LibCST in charge of "how," and put the test suite in charge of "is it still correct" — and you get something you can actually trust.

We also learned that **visible verification is the product.** An agent that silently refactors and hands you a diff to trust is just a faster way to introduce bugs. Rendering the gate log — especially the catch-and-rollback moment — is what turns "AI did something to my code" into "I watched it get checked." And "safe" needs to be defined precisely enough to enforce mechanically: malformed output (parse), style drift (ruff), type regression (pyright), behavior change (pytest) are four different failures that need four different responses.

## What's next

- **First-class cross-file move & consolidate engines.** Today the LLM can *reason* about moving code to a new module, and rope can execute reference-correct moves — we're wiring `move` and duplicate-`consolidate` through the verified spine as first-class transforms (consolidate is currently the honest gap).
- **Unify the agent campaign onto the engine spine** so every specialist (dead-code, imports, duplicates) runs through the same graph-aware, impact-scoped checker the complexity agent already uses.
- **Goal-aware planning** — let a run target "make it smaller" (dead code + dedup + simplify) vs. "restructure for readability," so the engine optimizes for what you actually want.
- **Multi-language** via the `LanguageAdapter` registry — TypeScript/Go gates as drop-in optional deps, no core changes.

## Built with

`python` · `jedi` · `rope` · `libcst` · `tree-sitter` · `ruff` · `autoflake` · `pyright` · `pytest` · `radon` · `redis` · `redisvl` · `anthropic` · `ollama` · `sentence-transformers` · `openai` · `typer` · `fastmcp` · `git`
