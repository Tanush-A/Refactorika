# Scope

Refactorika targets **small-to-medium Python codebases** — single-package projects and small multi-file/multi-package repos where structure is shallow enough to reason about with static analysis. The product is one coherent harness; the capabilities below ship as one system, sequenced by the build order at the bottom.

## The non-negotiable invariant

**A refactor changes shape, not behavior.** Every *mutating* action is proven behavior-preserving by the gate stack (`parse → ruff → pyright → pytest`) before it commits, and rolled back atomically on any failure. This is the whole trust angle — it holds for every kind of edit, including duplicate consolidation and dead-code removal.

## In Scope

Two classes of capability work together: **advisory** tools that find and explain (read-only), and **verified mutations** that fix (gated). Advisory tools surface opportunities → Claude proposes a concrete edit → the mutation entrypoint proves it safe and commits.

**Organization (verified mutation)**
- Split large files into logically grouped modules.
- Reorder and deduplicate imports (stdlib → third-party → local).
- Extract reusable helpers from bloated call sites.

**Complexity (verified mutation)**
- Break long functions into smaller, named units.
- Flatten deeply nested conditionals (early returns, guard clauses).
- Replace repeated blocks with extracted, parameterized functions.

**Duplicate & dead code (advisory → verified mutation)**
- **Detect** semantic + structural duplicates (`find_duplicates`) and unreachable symbols (`find_dead_code`), each ranked with a confidence score. Never auto-delete — always surface first.
- **Consolidate / remove** the confirmed ones as ordinary verified mutations: Claude proposes the deletion or merge, and `pytest` *proves* nothing breaks before it lands. We don't just *find* dead code — we *safely remove* it, proven by your own tests.

**Context & documentation (advisory)**
- Generate and self-update `.refactorika/context/<module>.md` (`generate_docs`) capturing purpose, key exports, dependents, and the architectural decisions/workarounds embedded in the code.

## Out of Scope

- Multi-language support (JavaScript, TypeScript, Go, etc.) — **Python only**.
- Large-scale architectural rewrites (e.g., monolith → microservices).
- **Any mutation that alters runtime behavior or a public API contract** — the invariant above, full stop.
- Test generation or coverage improvements (we *run* your tests as the safety net; we don't write them).
- Dependency management or `pyproject.toml` edits.

## Exploratory (not now)

- Large multi-package monorepos with deep package hierarchies.
- Framework-aware refactoring (Django, FastAPI request/response patterns).
- Additional language targets.
- Vector search tuned per-team on private embedding models.

## Build order (value-per-hour)

The harness is built as a vertical slice first, then broadened. Each step is demoable on its own.

1. **Verified-refactor loop** *(foundation, shipped)* — `analyze_file → apply_and_verify → commit/rollback` on a curated repo, one refactor kind end-to-end, gate stack green. This is the trust spine everything else hangs off.
2. **Duplicate detection** — highest demo impact; reuses the existing tree-sitter AST work. Add structural fingerprinting + the Redis vector index. Consolidation rides the existing gate stack.
3. **Dead-code analysis + verified removal** — graph-based reachability; independent of the embedding pipeline, can be built in parallel. Removal rides the gate stack.
4. **Living docs** — `generate_docs` emits `.refactorika/context/<module>.md` after each refactor session.

See [05-redis-iris.md](05-redis-iris.md) for the caching/embedding layer and [04-architecture.md](04-architecture.md) for the tool surface.
</content>
