# PRD: Edit Memory
**A convention-audit and guided-refactor layer for AI coding agents**

---

> **Scope tags:** Core components (§1–§11) are **[Initial]** (committed for v1) unless tagged **[Reach]** (stretch goals; built only if time allows and descoped first). The companion integrations (§12–§13) each carry their own tag.

## 1. Problem

When refactoring a pre-existing codebase, two things go wrong today:

1. **No one knows how inconsistent the codebase already is.** Different files, written at different times or by different people, follow different conventions (error handling, naming, structure). There's no tool that surfaces this before a refactor starts — teams find out by accident, mid-PR.
2. **AI coding agents make it worse, not better, at scale.** An agent refactoring file-by-file has no persistent sense of "what convention did we just establish two files ago," and no systematic way to check whether a change breaks call sites elsewhere in the repo. Existing tools (Cursor, Copilot, Aider) rely on static, human-written rules files (`.cursorrules`, `CLAUDE.md`) that don't reflect the codebase's actual current state and don't update themselves.

## 2. Goal

Build a tool that:
- Audits a codebase for a specific convention (e.g. error-handling style) and reports where it's inconsistent.
- Produces a safe, dependency-aware order to fix it.
- Guides an agent through the refactor, checking each edit against the target convention and flagging any missed call sites — without dumping the entire repo into context at every step.
- Creates context files (structural maps of the refactored codebase) so future developers and agents can understand it without re-deriving the structure (see §5.6).

## 3. Non-goals (for this build)

- General-purpose convention detection across arbitrary pattern types. Scoped to **one convention type** for v1 (error-handling style, e.g. exceptions vs `Result<T>`/explicit error returns).
- Full static type-checking or a true "find all usages" engine. Call-site detection will be best-effort (AST/grep-based), not IDE-grade.
- Multi-language support. Scoped to **TypeScript** for v1.
- Cross-session/persistent memory across multiple repo lifecycles **[Reach]**. Initial v1 is scoped to a single audit-and-refactor pass; the Redis long-term tier (§12) persists only within that run for Initial, with cross-session reuse as a Reach goal.

## 4. Target user

A developer or team with a legacy or partially-migrated codebase who wants to bring it into a single consistent pattern, and wants an AI agent to do the mechanical work safely rather than doing it by hand or trusting an agent unsupervised.

## 5. Core components

### 5.1 Convention Audit
- Input: a repository path.
- Process: parse files with `tree-sitter-typescript`, detect instances of the target convention type, classify each instance into a variant.
- **What counts as an error-handling instance (TypeScript):**
  - `throw_statement` and `try_statement` / `catch_clause` nodes (exception-style).
  - Functions whose **explicitly-annotated** return type is a `Result<T>`-style discriminated union (e.g. `{ ok: true; value: T } | { ok: false; error: E }`), or a name from a **configurable known-Result-type list** (`neverthrow`'s `Result`/`ResultAsync`, `fp-ts` `Either`, `ts-results`, plus local aliases).
  - Functions whose **explicitly-annotated** return type is a nullable/sentinel (`T | null`, `T | undefined`) *used as the error signal* — see the sentinel caveat below.
- **Async unwrapping:** before classifying, unwrap `Promise<X>` (and `async` function returns) so `Promise<Result<T>>` / `Promise<T | null>` are bucketed by their inner type rather than skipped.
- **Sentinel caveat:** `T | null` is often a legitimate "not found" rather than an error. v1 counts it as the `sentinel` variant only with a corroborating signal (e.g. function name, or a sibling throwing variant); otherwise it is reported separately as *ambiguous* and **not** counted as a deviation, to avoid inflating the inconsistency number.
- **Classification:** each instance is bucketed into one of three variants — `exception`, `result-type`, `sentinel` — and attributed to its enclosing function/file. **Mixed functions** (e.g. `throw` for programmer errors *and* a `Result` return for expected failures) are labeled `mixed` rather than force-fit into one bucket.
- **Detection engine (v1):** classification is **tree-sitter-only**, so it is scoped to *syntactically visible* types — **explicitly-annotated** return types and recognized type names. Inferred return types and aliases requiring cross-file resolution are out of scope for v1 (see §10).
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
3. **Call-site sweep** — after a successful edit, re-scan the *recorded* call sites (AST + grep) to confirm none were left in the old convention; surface any stragglers. Note: this catches incompletely-converted *known* sites; it cannot find sites the §5.2 detection never recorded (true false negatives), which are addressed only by the ground-truth eval (§7).
4. **Reject → re-propose loop** — on any gate failure, surface the failure reason to the agent and let it re-propose, up to a bounded retry count. (This defines the previously-undefined failure path in §5.3.)
5. **Per-edit audit log** — append a structured record (file, checks run, pass/fail, retry count, final diff) to the local JSON store, powering the demo dashboard.

### 5.6 Context File Generation **[Initial]**
- Input: completed (or in-progress) refactor results + the call-site map from §5.2.
- Process: emit a structured context file per module/directory (e.g. `.editmemory/context/<module>.md`) summarizing the now-canonical convention, key exported symbols, and their dependents.
- Output: committed context files that future developers and agents read instead of re-deriving structure — closing the loop on the "Edit Memory" name by persisting the audit's findings as durable, human- and agent-readable artifacts.
- Note: generated from data the audit/plan already compute, so marginal cost is low.

## 6. Architecture

- **Delivery form**: a plugin that hooks into an existing agent loop / IDE (Claude Code, Cursor, etc.) via a thin plugin SDK adapter, rather than being a standalone IDE or a separate server process. The plugin registers a set of actions/hooks (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `record_edit`) that the host agent invokes in-process during its loop. `verify_edit` runs the §5.5 gate pipeline; `run_typecheck` wraps `tsc --noEmit`; `confirm_convention` captures the human-confirm decision from §5.1. A pre-edit/post-edit hook lets the plugin gate edits the host agent proposes without the agent having to call the gate explicitly.
- **Fallback delivery form**: CLI (`editmemory audit <repo>`, `editmemory plan`, `editmemory check <diff>`) that works against git history/diffs directly, for use without a live agent loop wired up.
- **Storage**: local JSON file in the repo (audit results, confirmed rule definition, call-site map, per-edit verification log). Log schema per edit: `{ file, variant_before, variant_after, checks: { parse, typecheck, callsite_sweep }, retries, diff }`.

## 7. Success metrics (for the demo)

- Audit correctly identifies the dominant convention and flags deviating files on a constructed/curated demo repo with known, deliberate inconsistency.
- Guided execution catches at least one deliberately planted convention violation and one *planted, ground-truth-known* missed call site, live, in the demo.
- **Ground-truth eval:** on the curated demo repo (whose true call-site set is known), report call-site detection precision/recall. This is the honest source for any false-negative number — **not** Sentry (§13).
- **Every committed edit passes the parse + `tsc --noEmit` gate**; no edit is committed in a non-compiling state.
- **The reject → re-propose loop demonstrably recovers** from a deliberately planted bad edit (rollback + successful re-proposal), live, in the demo.
- **Context files (§5.6)** are generated for the refactored modules and accurately reflect the post-refactor convention and key dependents.
- Token usage for audit + refactor is a fraction of the realistic agent-loop baseline on the demo repo (§5.4). Scaling claims (sub-linear in repo size) require multiple repo sizes to demonstrate and are a **[Reach]** measurement.

## 8. Demo script

1. Show the demo repo: deliberately inconsistent error handling across ~10-15 files.
2. Run audit → show report (dominant pattern, deviating files).
3. Run plan → show ordered task list with call-site counts.
4. Run guided execution → watch 3-4 files get fixed; live catch of a violation and a *planted, ground-truth-known* missed call site.
5. **Plant a bad edit** → show the pre-edit/typecheck gate reject it, roll back, and the agent recover via the re-propose loop.
6. Show token-usage chart: Edit Memory vs the realistic agent-loop baseline.
7. Open a generated context file (§5.6) for a refactored module — show it accurately reflects the new convention and its dependents.

## 9. Build plan / time estimate (hackathon)

| Component | Estimate |
|---|---|
| Convention audit (TypeScript, error-handling) + human-confirm step | 4-6 hrs |
| Refactor plan / call-site detection (AST + grep) | 3-5 hrs |
| Guided execution + consistency checks | 2-3 hrs |
| Verification harness (parse gate, `tsc` gate, sweep, re-propose loop) | 2-3 hrs |
| Context efficiency layer + comparison metric | 2-3 hrs |
| Context file generation (§5.6) **[Initial]** | 1-2 hrs |
| Redis integration (§12) — storage, Agent Memory, Context Retriever, LangCache **[Initial]** | 3-5 hrs |
| Demo repo construction + dashboard | 3-5 hrs |
| **Total (Initial)** | **22-32 hrs** |
| Sentry integration (§13) — SDK + per-tool spans **[Reach]** | +1-2 hrs |

**Build order:** ship a vertical slice (one file, end-to-end: audit → confirm → plan → check → verify → commit) on a 2-file repo *before* broadening to 10-15 files. This guarantees a demoable artifact even if audit generalization lags.

## 10. Key risks

- **Generalization risk**: convention detection working reliably only on the curated demo repo, not arbitrary code. Mitigated by being explicit in the pitch about current scope (one language, one pattern type).
- **Call-site accuracy risk**: grep/LLM-based dependency tracking will have false negatives compared to a real IDE. Acceptable for demo if framed honestly.
- **Inferred/imported-type blind spot**: tree-sitter-only detection (§5.1) sees syntax, not resolved types, so functions with *inferred* return types or `Result` aliases defined in other files are missed or left unclassified. Accepted for v1 and framed honestly; the TypeScript compiler API would close this gap (future scope). The curated demo repo should use explicit annotations so the audit reflects true adoption.
- **Time risk**: audit step is the most open-ended; should be timeboxed hardest and descoped first if behind schedule.
- **Harness dependency risk**: the typecheck gate depends on the demo repo having a working `tsconfig.json` and a fast `tsc --noEmit`; large projects may make this slow. Mitigated by single-file-scope checking and keeping the demo repo small. Timebox the `tsc` integration.

## 11. Future scope (explicitly out of v1)

- Multiple convention types audited simultaneously.
- Persistent memory across sessions/repo lifecycle (the Reach upgrade of the Redis long-term tier, §12).
- Vector-search-based rule retrieval — valuable once many convention types exist; unnecessary for v1's single type (see §12.2).
- Incorporating human review corrections as a second rule source.
- Multi-language support.

## 12. Redis Iris Integration (companion note) — **[Initial]**

Describes how Redis Iris slots into the existing architecture (§6) **without changing project scope**.

### 12.1 Why Redis Iris fits

The Redis track judging criteria specifically calls out using Iris for agent memory, vector search, and context retrieval — not just caching. Edit Memory's core mechanism (a rule list that needs to be retrieved selectively, plus structured lookups like call-site tracking) maps directly onto Iris's actual components rather than needing a bolted-on justification.

### 12.2 Component mapping

- **Redis Agent Memory → the rule list**
  - Long-term memory tier stores inferred conventions as they're extracted during the audit and refactor. For **Initial**, this persists *within the current run*; **cross-session reuse across repo lifecycles is [Reach]** (consistent with §3/§11).
  - Replaces a flat JSON rule file with something queryable. Note: v1 has a single convention type, so selective retrieval has limited payoff initially — its value (pulling only the rules relevant to a file) scales with convention count (§11).
  - Session memory tier holds the in-progress refactor task list and execution log for the current run — gives you the ordered event log for free instead of building your own.
- **Redis Context Retriever → `check_convention` / `get_impact`**
  - Context Retriever's model is typed, chainable tool calls over structured data rather than one-shot vector retrieval — exactly the shape these two plugin actions already need.
  - Define structured lookups (e.g. "all call sites for function X," "current dominant convention for pattern Y") as Context Retriever tools. The agent invokes them mid-refactor the same way it would any other plugin action, and the retrieval logic doesn't have to be hand-rolled.
- **Redis LangCache → audit efficiency**
  - The audit step makes repeated classification calls across files ("does this file use exceptions or `Result<T>`?"). LangCache caches these — keyed on the *normalized AST signature* of the construct, **not** loose semantic similarity, to avoid false cache hits that would corrupt audit accuracy.
  - This becomes a clean, legitimate "Redis beyond caching" story: caching is one piece, not the whole pitch — agent memory and context retrieval do the structural work.
- **Vector search (underlying both Agent Memory and Context Retriever) — [Reach]**
  - v1's three fixed, AST-detectable variants are matched *exactly* (more accurate than fuzzy matching here). Semantic vector matching becomes useful only once many convention types exist; it is a Reach capability, not an Initial dependency.

### 12.3 Architecture note (relative to §6)

- Local JSON storage (as written in §6) becomes the fallback/offline mode.
- Primary mode for the demo: Redis Cloud instance backing Agent Memory (rules + session log) and Context Retriever (call-site/dependency lookups).
- Plugin actions/hooks (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `record_edit`) call into Redis under the hood instead of reading/writing local JSON.

### 12.4 Demo addition

Alongside the existing demo script (§8: audit → plan → guided execution → token chart), add:

- A short Redis Insight view showing the long-term memory entries building up live as conventions are extracted — makes the "memory" claim visible, not just asserted.
- A note on the token-usage chart distinguishing LLM-call savings from LangCache vs the structural savings from not reloading full files.

### 12.5 Risk

- Added infra dependency (Redis Cloud setup, account/connection) on top of the existing build risks. Budget setup time early — don't leave Redis provisioning to the last few hours.

## 13. Sentry Integration (companion note) — **[Reach]**

Describes how Sentry AI Agent Monitoring slots into the existing architecture (§6) **without changing project scope**. Read alongside the Redis Iris note (§12).

### 13.1 Why Sentry fits

The Sentry track rewards strong technical execution paired with observability/error monitoring, not just a working demo. Edit Memory runs an agent loop over many plugin action/hook invocations, and Sentry surfaces where those calls *throw, fail, or slow down* live — turning action-level reliability into a visible signal. (The call-site *false-negative* rate from §10 is measured separately by the §7 ground-truth eval, not by Sentry, which has no ground truth; the two are complementary.)

Sentry also directly supports instrumenting in-process agent/tool integrations (action executions, prompt retrievals, resource access), which matches Edit Memory's plugin delivery form (§6) without needing custom monitoring code.

### 13.2 Component mapping

- **Plugin action instrumentation → reliability of the core mechanism**
  - Instrument `check_convention`, `get_impact`, and `record_edit` individually.
  - Track per-action *error/exception* rate and latency — surfaces actions that throw or fail. (Note: Sentry **cannot** measure false negatives / silently-missed call sites, since it has no ground truth; that number comes from the §7 ground-truth eval, not Sentry.)
  - This makes action-level failures (one component of the §10 call-site risk) a measured, visible number instead of an assumption.
- **Trace view → demo asset**
  - A single end-to-end trace covers the audit → plan → guided execution pipeline: model calls, action executions, and plugin/host interactions in one view.
  - Useful on screen during the live demo as a literal trace of what happened during a refactor run, alongside the audit report and token chart already planned.
- **Token/cost tracking → second source for the efficiency metric**
  - Sentry's AI monitoring captures token usage and cost per model call automatically.
  - Gives a second, independently-sourced version of the token-usage comparison in §7 (Edit Memory vs the realistic agent-loop baseline), without building that measurement by hand.
- **Error tagging/grouping → audit and execution failure patterns**
  - Automatic grouping of similar failures across runs — useful if the audit step misclassifies a pattern repeatedly in a particular kind of file; surfaces that as a single grouped issue rather than scattered noise.

### 13.3 Architecture note (relative to §6)

- Sentry SDK initialized when the plugin loads, with tracing enabled (`tracesSampleRate`) and the relevant AI/agent integration for whichever model client the host uses.
- Plugin action calls (`check_convention`, `get_impact`, `record_edit`) get wrapped so each shows up as its own span — gives per-action failure rates, not just an aggregate.
- Setup is lightweight (SDK init + integration registration) relative to the Redis provisioning work — can be added late without much schedule risk.

### 13.4 Demo addition

Alongside the existing demo script (§8: audit → plan → guided execution → token chart) and the Redis Insight addition (§12.4):

- Show a Sentry trace of one full refactor run: audit call, plan generation, each guided edit, and the consistency checks, as a single connected trace.
- Show the per-action dashboard: `check_convention` and `get_impact` *error/exception* rates over the demo run, paired with the §7 ground-truth precision/recall numbers (the actual source for false-negative rate) — together substantiating the PRD's honesty about call-site detection being best-effort rather than IDE-grade.

### 13.5 Risk

- Minimal added risk — this is the lightest of the three integrations (PRD core, Redis, Sentry) to bolt on, and can be the first thing descoped back to "logs only" if time runs short without losing the core pitch.
