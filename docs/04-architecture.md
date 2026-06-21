# Architecture

## Delivery form

- **Primary: MCP server.** Exposes tools so Edit Memory plugs into existing MCP-compatible agents (Claude Code, Cursor, etc.) as a refactor plugin, rather than being a standalone IDE:
  - `run_audit` — runs the convention audit (§ [05-core-components.md](05-core-components.md))
  - `confirm_convention` — captures the human-confirm decision from the audit
  - `get_plan` — returns the ordered refactor task list
  - `check_convention` — checks a proposed edit against the confirmed target convention
  - `get_impact` — returns known call sites / dependents for a file or symbol
  - `verify_edit` — runs the full verification-harness gate pipeline
  - `run_typecheck` — wraps `tsc --noEmit`
  - `record_edit` — appends a structured record to the per-edit audit log
- **Fallback: CLI.** `editmemory audit <repo>`, `editmemory plan`, `editmemory check <diff>` — works against git history/diffs directly, for use without a live agent loop wired up.

## Storage

- **Local JSON** (fallback/offline mode) — audit results, confirmed rule definition, call-site map, per-edit verification log.
- **Redis Cloud** (primary mode for the demo) — backs Agent Memory (rules + session log) and Context Retriever (call-site/dependency lookups); the MCP tools call into Redis under the hood instead of reading/writing local JSON. See [06-redis-integration.md](06-redis-integration.md).

## End-to-end flow

```
repo path
  → run_audit            (parse + classify convention instances)
  → confirm_convention   (human confirms/overrides dominant variant)
  → get_plan             (ordered task list, fewest-dependents-first)
  → for each file in plan:
        agent proposes edit
        → check_convention + get_impact   (pre-commit checks)
        → verify_edit                     (parse gate → tsc gate → call-site sweep)
        → on failure: reject → re-propose (bounded retries)
        → on success: record_edit (audit log)
  → context file generation               (per refactored module/directory)
```

## Build order (stated in the PRD)

Ship a vertical slice — one file, end-to-end: audit → confirm → plan → check → verify → commit — on a 2-file repo *before* broadening to 10-15 files. This guarantees a demoable artifact even if audit generalization lags.
