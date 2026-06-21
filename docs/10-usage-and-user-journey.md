# Usage & User Journey

Refactorika supports **two interfaces over one shared core** — a standalone CLI for codebase owners with no existing agent workflow, and an MCP server for people who already drive an agent (Claude Code, Cursor) day-to-day. Both are thin wrappers around the same underlying logic (audit, plan, verify, context-gen — see [05-core-components.md](05-core-components.md)), so supporting both isn't double the work as long as that core is built interface-agnostic from the start (see [Implementation note](#implementation-note-keeping-both-easy) below).

## Flow 1 — Standalone CLI

For an owner who just wants to point a tool at their repo, no agent setup required.

```
$ refactorika audit .
```
- Parses the repo, classifies error-handling instances, proposes a dominant convention.
- Prints: dominant variant + % adoption, list of deviating files (file:line).

```
$ refactorika confirm --variant result-type
```
- Locks in the convention (or the CLI prompts you interactively if you just ran `audit` without `--variant`).

```
$ refactorika plan
```
- Prints the ordered task list (least-dependent files first) with call-site counts per file.

```
$ refactorika run
```
- Walks the plan file-by-file. Internally calls an LLM to propose each edit, then runs it through the verification harness (parse gate → `ruff` → `pyright` → `pytest` → call-site/handled-result sweep) before committing. Prints a running log: file, checks passed/failed, retries, diff.
- On a gate failure, retries up to a bounded count automatically; if still failing, skips the file and flags it for manual review rather than blocking the whole run.

```
$ refactorika context
```
- Generates `.refactorika/context/<module>.md` files summarizing the now-canonical convention per module.

Each step is also runnable standalone against a diff (`refactorika check <diff>`) for CI/pre-commit use, independent of the full run.

## Flow 2 — MCP plugin into an existing agent

For someone already working in Claude Code/Cursor who wants their agent's refactors constrained and verified instead of freelanced.

1. Add the Refactorika MCP server to the agent's MCP config.
2. In chat: *"Audit this repo's error-handling conventions and refactor it to be consistent."*
3. The agent calls `run_audit` → presents the report → calls `confirm_convention` once you approve → calls `get_plan` → for each file, proposes an edit and calls `check_convention` / `get_impact` / `verify_edit` before applying it → calls `record_edit` to log the result.
4. You watch the same gate failures/retries/successes, but inside your normal agent chat instead of a terminal log.

The behavior is identical to Flow 1 — same audit, same plan, same gates — the only difference is who's driving (the CLI orchestrates the loop itself in Flow 1; the agent orchestrates it, calling Refactorika as tools, in Flow 2).

## Implementation note: keeping both easy

This only stays cheap if the core logic lives in one library and both interfaces are thin shells over it:

- **One core module** exposes the operations (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `run_lint`, `run_tests`, `record_edit`, `generate_context_files`) as plain functions/classes with no CLI- or MCP-specific code mixed in.
- **The CLI** is a thin layer that parses argv, calls the core functions directly in-process, and formats output as terminal text — it does *not* need its own LLM-calling loop for edit proposals; it can still shell out to a model for the "propose an edit" step, but the verification/gating code is identical to what MCP uses.
- **The MCP server** is an equally thin layer that exposes the same core functions as MCP tools, with the *agent* (not Refactorika) responsible for proposing edits — Refactorika just verifies them.
- Storage (local JSON or Redis — see [06-redis-integration.md](06-redis-integration.md)) is read/written by the core module, so both interfaces see the same audit/plan/log state regardless of which one is driving.

The asymmetry to watch: in Flow 1, *Refactorika itself* must propose edits (it needs its own model call), since there's no external agent to do that part. In Flow 2, the *agent* proposes edits and Refactorika only checks them. That's the one piece of real logic that differs between the two interfaces — everything else (audit, plan, verify, context-gen) is shared as-is.
