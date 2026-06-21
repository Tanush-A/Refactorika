# Tech Stack

## Parsing & analysis
- **`tree-sitter-typescript`** — parses TypeScript source into an AST for convention detection (audit) and pre-edit validation (verification harness). Detects `throw_statement` / `try_statement` / `catch_clause` nodes, `Result<T>`-style discriminated union return types, and nullable/sentinel return types.
- **`tsc --noEmit`** — post-edit type check on touched files (project-scope or single-file scope where configured). Failing this rolls the edit back.
- **AST symbol search + grep fallback** — used for call-site / dependent detection when building the refactor plan and during the post-edit call-site sweep.

## Delivery / integration layer
- **MCP server** — the primary delivery form. Exposes tools (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `record_edit`) so Edit Memory plugs into existing MCP-compatible agents (Claude Code, Cursor, etc.) as a refactor plugin, rather than shipping as a standalone IDE.
- **CLI fallback** — `editmemory audit <repo>`, `editmemory plan`, `editmemory check <diff>` — works directly against git history/diffs without a live agent loop wired up.

## Storage
- **Local JSON** (fallback/offline mode) — audit results, confirmed rule definition, call-site map, per-edit verification log. Per-edit log schema: `{ file, variant_before, variant_after, checks: { parse, typecheck, callsite_sweep }, retries, diff }`.
- **Redis Cloud / Iris** (primary mode for the demo) — see [06-redis-integration.md](06-redis-integration.md) for the full component mapping:
  - **Agent Memory** — long-term tier for inferred conventions (the "rule list"); session tier for the in-progress task list and execution log.
  - **Context Retriever** — backs `check_convention` / `get_impact` as structured, chainable lookups (not vector search — v1's three convention variants are matched exactly).
  - **LangCache** — caches per-file classification calls during the audit, keyed on normalized AST signature (not semantic similarity, to avoid corrupting audit accuracy).

## Observability (stretch)
- **Sentry AI Agent Monitoring** [Reach] — instruments MCP tool calls (`check_convention`, `get_impact`, `record_edit`) individually for error/exception rate and latency; provides an end-to-end trace of the audit → plan → guided execution pipeline; second source for token/cost tracking. See [07-sentry-integration.md](07-sentry-integration.md).

## Why this stack

- Tree-sitter + grep over a full type-checker because v1 explicitly doesn't promise IDE-grade accuracy — it's framed honestly as best-effort (see [08-risks-and-scope.md](08-risks-and-scope.md)).
- MCP-first because the explicit positioning is "plugin for existing agent loops," not a standalone product — see [01-problem-and-purpose.md](01-problem-and-purpose.md).
- Redis Iris is chosen because its actual components (Agent Memory, Context Retriever, structured caching) map directly onto Edit Memory's existing mechanism (a retrievable rule list + structured call-site lookups), rather than being bolted on for a sponsor track.
