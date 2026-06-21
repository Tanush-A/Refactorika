# PRD: Edit Memory
**A convention-audit and guided-refactor layer for AI coding agents**

---

## 1. Problem

When refactoring a pre-existing codebase, two things go wrong today:

1. **No one knows how inconsistent the codebase already is.** Different files, written at different times or by different people, follow different conventions (error handling, naming, structure). There's no tool that surfaces this before a refactor starts — teams find out by accident, mid-PR.
2. **AI coding agents make it worse, not better, at scale.** An agent refactoring file-by-file has no persistent sense of "what convention did we just establish two files ago," and no systematic way to check whether a change breaks call sites elsewhere in the repo. Existing tools (Cursor, Copilot, Aider) rely on static, human-written rules files (`.cursorrules`, `CLAUDE.md`) that don't reflect the codebase's actual current state and don't update themselves.

## 2. Goal

Build a tool that:
- Audits a codebase for a specific convention (e.g. error-handling style) and reports where it's inconsistent.
- Produces a safe, dependency-aware order to fix it.
- Guides an agent through the refactor, checking each edit against the target convention and flagging any missed call sites — without dumping the entire repo into context at every step.
- Creates context files of the code structure for future software developers and agents to easily understand the refactored codebase.

## 3. Non-goals (for this build)

- General-purpose convention detection across arbitrary pattern types. Scoped to **one convention type** for v1 (error-handling style, e.g. exceptions vs `Result<T>`/explicit error returns).
- Full static type-checking or a true "find all usages" engine. Call-site detection will be best-effort (AST/grep-based), not IDE-grade.
- Multi-language support. Scoped to **TypeScript** for v1.
- Cross-session/persistent memory across multiple repo lifecycles. Scoped to a single audit-and-refactor pass.

## 4. Target user

A developer or team with a legacy or partially-migrated codebase who wants to bring it into a single consistent pattern, and wants an AI agent to do the mechanical work safely rather than doing it by hand or trusting an agent unsupervised.

## 5. Core components

### 5.1 Convention Audit
- Input: a repository path.
- Process: parse files with `tree-sitter-typescript`, detect instances of the target convention type, classify each instance into a variant.
- **What counts as an error-handling instance (TypeScript):**
  - `throw_statement` and `try_statement` / `catch_clause` nodes (exception-style).
  - Functions whose declared return type is a `Result<T>`-style discriminated union (e.g. `{ ok: true; value: T } | { ok: false; error: E }`, or `neverthrow`-style `Result`/`ResultAsync`).
  - Functions whose return type is a nullable/sentinel (`T | null`, `T | undefined`) used as the error signal.
- **Classification:** each instance is bucketed into one of three variants — `exception`, `result-type`, `sentinel` — and attributed to its enclosing function/file.
- **Human-confirm step:** the audit *proposes* the dominant convention; the user confirms or overrides it in one step before any plan is generated. This converts the riskiest LLM classification into a cheap confirmation and prevents downstream errors from propagating into the plan/execution.
- Output: a report — proposed dominant variant (pending confirmation), % adoption, list of deviating files with file:line references.

### 5.2 Refactor Plan
- Input: confirmed audit report.
- **v1 "call site" contract:** a call site is a *direct, same-language reference* — i.e. an `import`/`require` of the changed symbol plus a direct `call_expression` against it. Explicitly out of scope for v1: dynamic dispatch, re-exports/barrel files, runtime string-keyed access, and cross-language boundaries. These are known false-negative sources and are framed honestly rather than claimed as solved.
- Process: for each deviating file, identify call sites / dependents (AST symbol search, grep fallback) to determine safe ordering — files with fewer external dependents go first.
- Output: an ordered task list, one entry per file, with associated call-site list.

### 5.3 Guided Execution
- Input: refactor plan, one task at a time.
- Process: agent proposes an edit for the current file. Before applying, the edit is checked against the target convention (from audit) and cross-referenced against the known call sites for that file. Violations or missed call sites are surfaced before the edit is committed. The full pre/post-commit gating is defined in §5.5.
- Output: applied edits + a running log of what was checked and caught.

### 5.4 Context Efficiency Layer
- Audit and execution both avoid loading full file contents repeatedly. File state is represented as a target convention summary + structural patch log rather than repeated full-file text.
- **Baseline definition:** the comparison baseline is a *realistic* agent loop (per-file diffs + retrieved snippets on demand), **not** a strawman that re-dumps every file every step. The metric is honest only against a credible baseline.
- **Primary framing is correctness, not efficiency.** The headline win is caught convention violations and caught missed call sites; token savings are reported as a secondary benefit.
- Metric tracked: tokens used for audit + refactor vs the realistic baseline above.

### 5.5 Verification Harness
Automated guardrails layered on top of guided execution. Every proposed edit passes through this pipeline before it is committed:
1. **Pre-edit gate** — the proposed edit is parsed with `tree-sitter-typescript`; reject if it fails to parse or does not match the confirmed target variant.
2. **Post-edit type check** — run `tsc --noEmit` (project scope, or single-file scope where configured) on touched files; if it fails, roll the edit back.
3. **Call-site sweep** — after a successful edit, re-scan the recorded call sites (AST + grep) to confirm none were left in the old convention; surface any stragglers.
4. **Reject → re-propose loop** — on any gate failure, sursface the failure reason to the agent and let it re-propose, up to a bounded retry count. (This defines the previously-undefined failure path in §5.3.)
5. **Per-edit audit log** — append a structured record (file, checks run, pass/fail, retry count, final diff) to the local JSON store, powering the demo dashboard.

## 6. Architecture

- **Delivery form**: MCP server exposing tools (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `record_edit`) so it plugs into existing MCP-compatible agents (Claude Code, Cursor, etc.) rather than being a standalone IDE. `verify_edit` runs the §5.5 gate pipeline; `run_typecheck` wraps `tsc --noEmit`; `confirm_convention` captures the human-confirm decision from §5.1.
- **Fallback delivery form**: CLI (`editmemory audit <repo>`, `editmemory plan`, `editmemory check <diff>`) that works against git history/diffs directly, for use without a live agent loop wired up.
- **Storage**: local JSON file in the repo (audit results, confirmed rule definition, call-site map, per-edit verification log). Log schema per edit: `{ file, variant_before, variant_after, checks: { parse, typecheck, callsite_sweep }, retries, diff }`.

## 7. Success metrics (for the demo)

- Audit correctly identifies the dominant convention and flags deviating files on a constructed/curated demo repo with known, deliberate inconsistency.
- Guided execution catches at least one deliberately planted convention violation and one deliberately planted missed call site, live, in the demo.
- **Every committed edit passes the parse + `tsc --noEmit` gate**; no edit is committed in a non-compiling state.
- **The reject → re-propose loop demonstrably recovers** from a deliberately planted bad edit (rollback + successful re-proposal), live, in the demo.
- Token usage for audit + refactor stays flat/sub-linear relative to repo size, shown against the realistic agent-loop baseline (§5.4).

## 8. Demo script

1. Show the demo repo: deliberately inconsistent error handling across ~10-15 files.
2. Run audit → show report (dominant pattern, deviating files).
3. Run plan → show ordered task list with call-site counts.
4. Run guided execution → watch 3-4 files get fixed; live catch of a violation and a missed call site.
5. **Plant a bad edit** → show the pre-edit/typecheck gate reject it, roll back, and the agent recover via the re-propose loop.
6. Show token-usage chart: Edit Memory vs the realistic agent-loop baseline.

## 9. Build plan / time estimate (hackathon)

| Component | Estimate |
|---|---|
| Convention audit (TypeScript, error-handling) + human-confirm step | 4-6 hrs |
| Refactor plan / call-site detection (AST + grep) | 3-5 hrs |
| Guided execution + consistency checks | 2-3 hrs |
| Verification harness (parse gate, `tsc` gate, sweep, re-propose loop) | 2-3 hrs |
| Context efficiency layer + comparison metric | 2-3 hrs |
| Demo repo construction + dashboard | 3-5 hrs |
| **Total** | **18-25 hrs** |

**Build order:** ship a vertical slice (one file, end-to-end: audit → confirm → plan → check → verify → commit) on a 2-file repo *before* broadening to 10-15 files. This guarantees a demoable artifact even if audit generalization lags.

## 10. Key risks

- **Generalization risk**: convention detection working reliably only on the curated demo repo, not arbitrary code. Mitigated by being explicit in the pitch about current scope (one language, one pattern type).
- **Call-site accuracy risk**: grep/LLM-based dependency tracking will have false negatives compared to a real IDE. Acceptable for demo if framed honestly.
- **Time risk**: audit step is the most open-ended; should be timeboxed hardest and descoped first if behind schedule.
- **Harness dependency risk**: the typecheck gate depends on the demo repo having a working `tsconfig.json` and a fast `tsc --noEmit`; large projects may make this slow. Mitigated by single-file-scope checking and keeping the demo repo small. Timebox the `tsc` integration.

## 11. Future scope (explicitly out of v1)

- Multiple convention types audited simultaneously.
- Persistent memory across sessions/repo lifecycle.
- Incorporating human review corrections as a second rule source.
- Multi-language support.
