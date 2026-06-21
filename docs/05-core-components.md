# Core Components

> Tags: components below are **[Initial]** (committed for v1) unless marked **[Reach]** (stretch, descoped first if behind schedule).

## 5.1 Convention Audit
- **Input:** a repository path.
- **Process:** parse files with `tree-sitter-python`, detect instances of the target convention type, classify each instance into a variant.
- **What counts as an error-handling instance (Python):**
  - `raise_statement` and `try_statement` / `except_clause` nodes (exception-style).
  - Functions whose **explicitly-annotated** return type is a `Result`-style object — a name from a **configurable known-Result-type list** (the `returns` library's `Result`/`Maybe`, plus local `Result`/`Either` classes and aliases).
  - Functions whose **explicitly-annotated** return type is a sentinel (`Optional[T]`, `T | None`) *used as the error signal* — see the sentinel caveat below.
- **Async unwrapping:** before classifying, unwrap `Awaitable[X]` / `Coroutine[..., X]` (and `async def` returns) so `Awaitable[Result[T]]` / `Optional[T]` from coroutines are bucketed by their inner type rather than skipped.
- **Sentinel caveat:** `Optional[T]` / `T | None` is often a legitimate "not found" rather than an error. v1 counts it as the `sentinel` variant only with a corroborating signal (e.g. function name, or a sibling raising variant); otherwise it is reported separately as *ambiguous* and **not** counted as a deviation, to avoid inflating the inconsistency number.
- **Classification:** each instance is bucketed into one of three variants — `exception`, `result-type`, `sentinel` — and attributed to its enclosing function/file. **Mixed functions** (e.g. `raise` for programmer errors *and* a `Result`/`None` return for expected failures) are labeled `mixed` rather than force-fit into one bucket.
- **Detection engine (v1):** classification is **tree-sitter-only**, so it is scoped to *syntactically visible* types — **explicitly-annotated** return types (function annotations) and recognized type names. Unannotated functions and aliases requiring cross-file resolution are out of scope for v1 (see [08-risks-and-scope.md](08-risks-and-scope.md)).
- **Human-confirm step:** the audit *proposes* the dominant convention; the user confirms or overrides it in one step before any plan is generated. This converts the riskiest LLM classification into a cheap confirmation and prevents downstream errors from propagating into the plan/execution.
- **Output:** a report — proposed dominant variant (pending confirmation), % adoption, list of deviating files with file:line references.

## 5.2 Refactor Plan
- **Input:** confirmed audit report.
- **v1 "call site" contract:** a call site is a *direct, same-language reference* — i.e. an `import` / `from … import` of the changed symbol plus a direct `call` against it. Explicitly out of scope for v1: dynamic dispatch, `__getattr__`/`getattr` string-keyed access, re-exports via `__init__.py`, monkeypatching, and cross-language boundaries. These are known false-negative sources and are framed honestly rather than claimed as solved.
- **Process:** for each deviating file, identify call sites / dependents (AST symbol search, grep fallback) to determine safe ordering — files with fewer external dependents go first.
- **Output:** an ordered task list, one entry per file, with associated call-site list.

## 5.3 Guided Execution
- **Input:** refactor plan, one task at a time.
- **Process:** agent proposes an edit for the current file. Before applying, the edit is checked against the target convention (from audit) and cross-referenced against the known call sites for that file. Violations or missed call sites are surfaced before the edit is committed. Full pre/post-commit gating is defined in [05a-verification-harness.md](05a-verification-harness.md).
- **Output:** applied edits + a running log of what was checked and caught.

## 5.4 Context Efficiency Layer
- Audit and execution both avoid loading full file contents repeatedly. File state is represented as a target-convention summary + structural patch log rather than repeated full-file text.
- **Baseline definition:** the comparison baseline is a *realistic* agent loop (per-file diffs + retrieved snippets on demand), **not** a strawman that re-dumps every file every step. The metric is honest only against a credible baseline.
- **Primary framing is correctness, not efficiency.** The headline win is caught convention violations and caught missed call sites; token savings are reported as a secondary benefit.
- **Metric tracked:** tokens used for audit + refactor vs the realistic baseline above.

## 5.5 Verification Harness

See [05a-verification-harness.md](05a-verification-harness.md) for the full gate pipeline (pre-edit parse gate, post-edit typecheck, call-site sweep, reject → re-propose loop, per-edit audit log).

## 5.6 Context File Generation **[Initial]**
- **Input:** completed (or in-progress) refactor results + the call-site map from §5.2.
- **Process:** emit a structured context file per module/directory (e.g. `.editmemory/context/<module>.md`) summarizing the now-canonical convention, key exported symbols, and their dependents.
- **Output:** committed context files that future developers and agents read instead of re-deriving structure — closing the loop on the "Edit Memory" name by persisting the audit's findings as durable, human- and agent-readable artifacts.
- **Note:** generated from data the audit/plan already compute, so marginal cost is low.
