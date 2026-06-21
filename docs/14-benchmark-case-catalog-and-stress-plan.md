# Benchmark Case Catalog and Refactoring Stress Plan

This document describes the benchmark code as it exists today and catalogs a
large expansion set. It is a design document, not a claim that the proposed
cases have already been implemented.

## What the repository currently benchmarks

Refactorika has two benchmarks with different causal questions. Their results
must not be pooled.

| Benchmark | Runner | Experimental question | OFF arm | ON arm |
|---|---|---|---|---|
| Full system | `eval/full_system_bench.py` | Does Refactorika help an agent discover and land a worthwhile refactor from an underspecified request? | Independently selects and emits a complete patch in one model call | Receives audit/planning context, emits an independent patch, then uses atomic gates and diagnostic-guided repair |
| Shared-patch ablation | `eval/harness_bench.py` | Do verification, rollback, and repair improve the fate of an already-selected patch? | Writes the shared initial patch directly | Routes that same patch through parse, Ruff, Pyright, visible pytest, rollback, and repair |

The full-system benchmark is the product benchmark. The shared-patch benchmark
isolates a narrower safety mechanism.

## Full-system benchmark structure

### Case discovery and normalization

`eval/full_system_cases/__init__.py` combines three fixture families into
`ALL_CASES`. `adapt_case()` converts each family to a `CaseAdapter` containing:

- the agent-visible baseline files;
- separately held hidden tests;
- the exact user prompt, always `refactor this codebase`;
- paths expected to change and paths considered in scope;
- the original typed case object used by its structural grader.

Only the baseline is materialized. Files below `tests/oracle/`, Refactorika
state, and Git state are excluded from model snapshots. Python, Markdown, JSON,
and TOML files are visible. Agents may return only complete contents for changed
Python files and may not edit tests.

### Arms

Every `(case, trial)` starts from isolated copies of the same baseline.

- `off`: the model sees the generic request and full visible snapshot. It must
  choose a refactor and return a JSON path-to-content patch in one call. The
  patch ships without Refactorika gates and is then graded.
- `on`: Refactorika runs its audit and plan builder, includes discovered
  architecture notes, and asks the same model for a scoped patch. The patch is
  applied atomically through parse, lint, type, and visible-test gates. Failed
  attempts roll back and may receive exact diagnostics for bounded repair.
- `agentic` (optional in the current runner): a tool-using model can list, read,
  write, and run commands in its own repository copy. This is a third arm, not
  a replacement for the one-call paired OFF/ON comparison.

OFF and ON each receive one initial model call. ON can consume additional calls
after a rejected attempt, so initial and final outcomes are reported separately.

### Grading

Final grading has independent behavioral and structural components:

1. Hidden tests are injected only after a candidate has landed.
2. Pytest runs visible and hidden tests together.
3. A case-specific AST or source-delta check evaluates whether the intended
   refactoring effect occurred.
4. `correct_landed` requires an effective landed edit, passing behavior, and
   passing structure.

Passing behavior but missing the structural target is an incomplete refactor.
Failing behavior after shipping is a regression. ON can instead safely escalate
after exhausting repair attempts.

### Foundational nine cases

| Family | Case | Intended refactor | Principal trap | Hidden/structural oracle |
|---|---|---|---|---|
| Behavior | `rounding_order` | Consolidate duplicated validation and price calculation | Reordering integer discount and tax changes truncation at small values | Boundary values, validation parity, and a shared private helper |
| Behavior | `guard_clause_continue` | Flatten a triple-nested event filter | `return` aborts the collection while `continue` skips one item | Mixed invalid/valid sequences and AST evidence of loop `continue` |
| Behavior | `near_duplicate_semantics` | Extract duplicated account filtering | Trial and paid flows differ at expiry equality and only trials sort output | Boundary, ordering, malformed records, and a shared helper |
| Multi-file | `rename_with_call_sites_and_reexport` | Rename `clean_email` internally | Attribute-style caller or package compatibility export is missed | New definition, both callers, and legacy export |
| Multi-file | `move_symbol_and_update_imports` | Move formatting out of billing | One of two import styles remains pointed at the old module | New location, absence at old location, both consumers, behavior |
| Multi-file | `internal_rename_preserves_public_api` | Adopt internal `build_*` convention | Public `make_slug` import is broken | New internal symbol, updated internal caller, legacy package API |
| Recovery | `type_clean_threshold_regression` | Extract shipping-fee policy | `>=` silently becomes `>` at the free-shipping boundary | Visible threshold failure and hidden adjacent values |
| Recovery | `nullable_return_requires_targeted_repair` | Refactor inventory availability | Return widens to `int | None`, breaking caller contract | Strict Pyright diagnostic and negative-inventory behavior |
| Recovery | `repeated_invalid_repairs_escalate` | Make a source refactor without breaking syntax | Repeated proposals omit a colon | Parse diagnostics, atomic rollback, and bounded escalation |

### Implemented stress expansion

The suite now contains 47 cases: the foundational nine plus 38 stress cases.

| Module | Cases | Coverage |
|---|---:|---|
| `stress.py` | 8 | Aliased rename, keyword API compatibility, nested breaks, mutation ownership, exception causes, missing sentinels, generated decoys, stable sorting |
| `stress_semantics_extra.py` | 10 | Numeric thresholds, rounding sequence, loop guards, alias ownership, error payloads/chaining, generator cleanup, recursion cycles, grouping order, cleanup semantics |
| `stress_contracts_extra.py` | 10 | Import topology, circular-sensitive moves, exports, signatures, plugins, dataclasses, protocols, generated exclusions, symbol decoys, enum values |
| `stress_systems_extra.py` | 10 | Async cancellation/order, transactions, serialization, timezones, wire enums, filesystem safety, middleware order, caches and resource cleanup |

All 38 use the same generic user prompt, architecture evidence, visible tests,
held-out behavioral tests, and declarative structural expectations. Calibration
requires every baseline to pass behavior while still missing its target
structure. The remaining catalog entries below are backlog, not implemented
fixtures.

### Shared-patch benchmark substrate

`eval/harness_tasks.py` generates ten variants of one transformation: convert a
service function from raising `ValueError` to returning `Result`, and update its
caller. The variants are `withdraw`, `reserve`, `discount`, `parse_port`,
`page_end`, `retry_delay`, `quota_left`, `shipping`, `score`, and `batch_count`.

Each task has one reference-good patch and four bad controls:

- a visible behavior error;
- a missed caller;
- a syntax, unresolved-name, or return-type gate defect;
- a held-out-only boundary error that demonstrates residual gate risk.

Calibration requires all ten good and forty bad controls to receive their
expected oracle labels before model results are accepted.

### Benchmark unit tests

`tests/test_full_system_bench.py` currently verifies fixture normalization,
hidden-oracle isolation, independent initial proposals, baseline calibration,
separation of behavior and structure, repair accounting, gate attribution,
rollback integrity, pricing, and infrastructure-failure invalidation. These are
runner tests; they do not increase semantic refactoring coverage.

## Current limitations to fix while expanding

1. **Repository scale is too small.** Nine toy repositories do not test search,
   context selection, dependency fan-out, or irrelevant-code resistance.
2. **Python is the only language.** Claims must remain Python-specific until
   additional parsers, gates, and oracles exist.
3. **Structural grading is narrow.** Behavior cases use case-name-specific AST
   rules, and recovery cases largely accept any source delta as structure.
4. **Targets can be easy to infer.** Architecture notes and deprecation comments
   sometimes state the answer nearly verbatim, reducing discovery difficulty.
5. **The shared-patch cases are low-diversity templates.** Ten arithmetic
   variants measure repeatability more than ten independent refactoring skills.
6. **No realistic framework contracts.** There are no async, ORM, HTTP, CLI,
   serialization, plugin, migration, or concurrency cases.
7. **Limited compatibility surface.** Package exports are tested, but signatures,
   keyword callers, pickled names, schemas, entry points, and wire formats are not.
8. **No performance or resource oracle.** A behavior-preserving refactor can add
   queries, scans, allocations, blocking calls, or file-descriptor leaks and pass.
9. **No metamorphic or property oracle.** Most hidden tests are example-based.
10. **Scope metrics inherit expected paths.** They should be treated as evaluator
    diagnostics, not ground truth that every valid alternative must touch.

## Design standard for every new case

Every added case should specify the following before implementation:

- a generic initial prompt and an agent-visible repository with at least two
  plausible refactoring opportunities;
- a rationale for why the intended opportunity is highest value;
- visible tests that establish ordinary behavior without revealing every trap;
- hidden example tests plus property, metamorphic, or differential checks where
  practical;
- machine-checkable structural expectations that allow multiple valid designs;
- explicit compatibility, scope, and forbidden-edit expectations;
- a reference implementation and at least four calibrated bad patches;
- expected failure gates and diagnostic substrings for repair cases;
- deterministic execution with no network and controlled clocks/randomness;
- a size, fan-out, semantic-risk, and discovery-difficulty label.

Cases should challenge both arms. Difficulty must come from repository evidence,
semantic constraints, and incomplete visible coverage—not from ambiguous grading
or hidden requirements that a competent engineer could not infer.

## Proposed stress-test catalog

The following 120-case catalog is the long-term coverage target. Thirty-eight
implemented cases cover many of these failure modes, although their fixture
names do not map one-to-one to catalog IDs. The short oracle note identifies
the minimum hidden check; remaining implementations should add calibrated
reference patches.

### A. Numeric, boundary, and representation semantics

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| NUM-01 | Extract tiered pricing without changing inclusive/exclusive bracket edges | Every boundary and adjacent integer |
| NUM-02 | Consolidate currency conversion while preserving `Decimal` quantization and rounding mode | Half-even/half-up counterexamples |
| NUM-03 | Replace float accumulation with a helper without changing NaN and infinity handling | NaN, infinities, signed zero |
| NUM-04 | Deduplicate percentage calculations with negative amounts and floor division | Positive/negative quotient properties |
| NUM-05 | Extract pagination math without introducing an off-by-one final page | Empty, exact multiple, remainder |
| NUM-06 | Centralize byte-size formatting while preserving 1000 versus 1024 units | Thresholds around every suffix |
| NUM-07 | Refactor rate limiting without overflow or clock-unit confusion | Nanosecond/second conversions and cap |
| NUM-08 | Share coordinate normalization without swapping latitude and longitude | Range properties and asymmetric points |

### B. Control flow, iteration, and short-circuiting

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| FLOW-01 | Flatten nested loops while preserving inner versus outer `break` | Multi-batch sequence with late match |
| FLOW-02 | Extract predicate without losing boolean short-circuit side effects | Call-count and exception sentinel |
| FLOW-03 | Replace loop with comprehension while preserving evaluation order | Stateful iterator trace |
| FLOW-04 | Consolidate retry loops without catching cancellation/system-exit signals | `CancelledError` propagation |
| FLOW-05 | Simplify `try/else/finally` without moving success-only work | Success, failure, cleanup trace |
| FLOW-06 | Extract generator helper without changing laziness | Partial consumption and raised sentinel |
| FLOW-07 | Refactor recursive traversal while preserving cycle detection and visit order | Cyclic graph and ordered visitation |
| FLOW-08 | Merge duplicated state machines without accepting illegal transitions | Exhaustive state/event transition table |

### C. Mutation, aliasing, and object lifetime

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| MUT-01 | Extract list normalization without mutating caller-owned input | Identity and before/after snapshot |
| MUT-02 | Replace copy logic without turning deep copies into shallow copies | Nested mutation isolation |
| MUT-03 | Consolidate dataclass defaults without introducing shared mutable state | Two-instance independence |
| MUT-04 | Refactor cache population without changing object identity guarantees | Repeated-call `is` checks |
| MUT-05 | Move cleanup into context manager without double-closing resources | Close count on all exit paths |
| MUT-06 | Extract dictionary merge while preserving insertion order | Conflicting keys and ordered output |
| MUT-07 | Refactor descriptor-backed fields without bypassing setter validation | Assignment trace and invalid values |
| MUT-08 | Simplify weak-reference registry without retaining objects | Forced GC and registry size |

### D. Exceptions, errors, and diagnostics

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| ERR-01 | Unify exception translation while preserving exact chained cause | Type, message, and `__cause__` |
| ERR-02 | Extract validation without changing which invalid field fails first | Multiple-invalid-input precedence |
| ERR-03 | Convert exceptions to `Result` across three caller strategies | Fallback, propagation, aggregation |
| ERR-04 | Merge cleanup handlers without suppressing the original exception | Primary versus cleanup exception |
| ERR-05 | Centralize logging without duplicating or losing one log event | Captured log count and fields |
| ERR-06 | Refactor parser fallback while distinguishing missing from malformed input | Separate error classes and messages |
| ERR-07 | Move retry classification without retrying permanent failures | Attempt count by exception subtype |
| ERR-08 | Consolidate batch errors while preserving partial-success ordering | Mixed success/failure result list |

### E. Multi-file symbols, imports, and dependency topology

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| DEP-01 | Rename a symbol used through direct, aliased, qualified, and local imports | All four call paths |
| DEP-02 | Move a class while avoiding a new circular import | Cold import in both module orders |
| DEP-03 | Split a module while maintaining lazy package imports | Import trace and public attributes |
| DEP-04 | Merge utilities while preserving plugin modules that import old paths | Dynamic import by string |
| DEP-05 | Rename protocol method across implementations and mocks | Runtime dispatch and typecheck |
| DEP-06 | Move a constant referenced in annotations and default arguments | Import, defaults, and annotations |
| DEP-07 | Extract shared base without changing method-resolution order | Diamond inheritance behavior |
| DEP-08 | Replace import-time registration with explicit registry assembly | Registry contents and import side effects |

### F. Public API and backward compatibility

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| API-01 | Rename a function while preserving positional and keyword callers | Old/new name and keyword invocation |
| API-02 | Add a parameter object while retaining legacy signature defaults | `inspect.signature` and calls |
| API-03 | Refactor a class without changing dataclass field/order contract | Construction, repr, equality, fields |
| API-04 | Move an enum while preserving pickle-qualified compatibility | Round-trip old pickle fixture |
| API-05 | Internalize a helper while retaining `__all__` and package import | Star import and direct import |
| API-06 | Replace named tuple internally without changing tuple unpacking | Indexing, unpacking, attribute access |
| API-07 | Consolidate overloads while preserving static inferred types | Pyright reveal-type fixture |
| API-08 | Rename CLI internals without changing flags, exit codes, or help text | Golden CLI invocation matrix |

### G. Async, concurrency, and temporal behavior

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| ASYNC-01 | Extract async retry helper without replacing `await` with blocking sleep | Fake-clock schedule and loop responsiveness |
| ASYNC-02 | Deduplicate task fan-out while preserving input-order results | Out-of-order completions |
| ASYNC-03 | Refactor lock scope without introducing a race or deadlock | Deterministic barrier interleaving |
| ASYNC-04 | Move cancellation cleanup without swallowing cancellation | Cancellation propagation and cleanup |
| ASYNC-05 | Consolidate async context managers without leaking connections | Pool checkout/checkin counts |
| ASYNC-06 | Refactor timeout layers while preserving inner versus outer timeout errors | Controlled virtual clock |
| ASYNC-07 | Replace queue worker duplication without losing `task_done` | Queue join and failure paths |
| ASYNC-08 | Extract thread-safe memoization without duplicate computation | Concurrent call count |

### H. Persistence, transactions, and ORM behavior

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| DB-01 | Extract repository helper without moving a write outside transaction | Rollback after injected failure |
| DB-02 | Consolidate query paths without introducing N+1 queries | Query-count assertion at scale |
| DB-03 | Rename model field while preserving serialized/database column name | Schema and round-trip fixture |
| DB-04 | Split service layer without committing partial multi-row updates | Atomicity under second-write failure |
| DB-05 | Refactor optimistic locking without dropping version predicate | Concurrent stale update rejection |
| DB-06 | Centralize soft-delete filtering without hiding admin queries | Default/admin visibility matrix |
| DB-07 | Move cascade logic without changing delete/orphan semantics | Relationship lifecycle checks |
| DB-08 | Extract bulk operation without changing duplicate/conflict behavior | Mixed existing/new keys |

### I. Serialization, schemas, and wire contracts

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| SER-01 | Deduplicate JSON encoding while preserving omitted versus explicit null | Golden payload pairs |
| SER-02 | Rename internal field without changing external alias | Parse and dump schema snapshots |
| SER-03 | Extract timestamp codec while preserving timezone and `Z` formatting | Offset, UTC, naive rejection |
| SER-04 | Consolidate enum serialization without switching name/value representation | Golden wire values |
| SER-05 | Refactor versioned payload migration while preserving old versions | Fixtures for every schema version |
| SER-06 | Move binary framing helper without changing endian or checksum order | Golden bytes and corrupt frames |
| SER-07 | Share CSV parser while preserving quoted newlines and empty fields | RFC-style edge fixtures |
| SER-08 | Refactor recursive serializer without changing cycle error path | Nested objects and cycle diagnostic |

### J. Filesystem, paths, and resource safety

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| FS-01 | Consolidate path validation without allowing traversal or symlink escape | Temp-tree escape attempts |
| FS-02 | Extract atomic writer without losing fsync/replace semantics | Injected crash-stage simulation |
| FS-03 | Refactor text loading while preserving encoding fallback and BOM handling | Multiple encoded fixtures |
| FS-04 | Move glob filtering without changing dotfile or case behavior | Platform-normalized file matrix |
| FS-05 | Share archive extraction without zip-slip vulnerability | Malicious member paths |
| FS-06 | Refactor file iterator without leaking descriptors on early stop | Descriptor count after partial consume |
| FS-07 | Consolidate config lookup while preserving precedence | CLI/env/user/project matrix |
| FS-08 | Extract temporary workspace manager without deleting caller paths | Ownership and cleanup cases |

### K. Framework and integration contracts

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| FW-01 | Extract FastAPI dependency without changing status/body/header contract | HTTP golden responses |
| FW-02 | Consolidate middleware while preserving order and exception handling | Ordered trace across failure paths |
| FW-03 | Move Flask blueprint handlers without changing endpoint names | `url_for` and route map |
| FW-04 | Refactor Django signal logic without duplicate registration | Handler call count after repeated import |
| FW-05 | Split Click commands without changing command tree/help/options | CLI golden snapshots |
| FW-06 | Move pytest fixtures without widening scope or changing teardown order | Invocation and teardown trace |
| FW-07 | Refactor Pydantic models without changing validation aliases/errors | Schema and error location snapshots |
| FW-08 | Consolidate logging config without duplicating handlers on reload | Reload and emitted-record counts |

### L. Types, generics, and dynamic Python features

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| TYPE-01 | Extract generic mapping helper without collapsing type variables to `Any` | Strict Pyright reveal types |
| TYPE-02 | Refactor overload implementation while retaining all overload contracts | Positive and negative type fixtures |
| TYPE-03 | Move protocol without creating runtime protocol-check differences | Static check plus `isinstance` |
| TYPE-04 | Consolidate decorators while preserving `ParamSpec` signatures | Signature and type inference |
| TYPE-05 | Refactor class factory without breaking `TypeVar` subclass return | Static inferred subclass type |
| TYPE-06 | Replace sentinel logic without confusing `None` with missing | Runtime matrix and narrowed types |
| TYPE-07 | Move forward-referenced types without import-time annotation failure | Cold imports under annotation modes |
| TYPE-08 | Extract attribute proxy without breaking `__getattr__` fallback | Missing/present/private attributes |

### M. Security and trust boundaries

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| SEC-01 | Consolidate authorization checks without moving them after data access | Unauthorized access trace |
| SEC-02 | Extract redirect validation without allowing scheme-relative URLs | Malicious URL corpus |
| SEC-03 | Refactor command construction without reintroducing shell injection | Metacharacter arguments and call spy |
| SEC-04 | Share SQL filters without string interpolation | Query/parameter capture |
| SEC-05 | Move token comparison without losing constant-time primitive | Monkeypatched primitive invocation |
| SEC-06 | Consolidate secret redaction without leaking nested/header variants | Structured sensitive payload corpus |
| SEC-07 | Refactor archive/upload validation without TOCTOU gap | Mutating file double simulation |
| SEC-08 | Extract tenant scoping without cross-tenant cache collisions | Same IDs in two tenants |

### N. Performance and algorithmic behavior

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| PERF-01 | Refactor lookup code while retaining linear rather than quadratic scaling | Operation-count bound |
| PERF-02 | Consolidate database loaders without adding N+1 calls | Fixed query-count ceiling |
| PERF-03 | Extract streaming transform without materializing whole input | Bounded-memory iterator spy |
| PERF-04 | Share regex logic without recompiling per item | Compile-call count |
| PERF-05 | Refactor cache key construction without reducing hit rate | Backend-call count and collisions |
| PERF-06 | Move batching logic without changing max batch size | Batch-size and call-count matrix |
| PERF-07 | Simplify sorting without losing stable tie ordering | Equal-key identity order |
| PERF-08 | Refactor recursive algorithm without losing memoization | Evaluation-count ceiling |

### O. Discovery, scope, and adversarial repositories

| ID | Challenge and likely trap | Minimum hidden oracle |
|---|---|---|
| DISC-01 | Choose one high-value duplicate among several cosmetic duplicates | Intended effect plus no unrelated churn |
| DISC-02 | Follow architecture guidance split across README, ADR, and deprecation warning | Multi-file target and compatibility |
| DISC-03 | Ignore a tempting generated/vendor copy of the target symbol | Generated tree unchanged |
| DISC-04 | Distinguish dead code from a dynamically loaded plugin | Plugin discovery by entry-point string |
| DISC-05 | Refactor across 30 files with only three true call sites | Call-site recall and edit precision |
| DISC-06 | Handle two same-named symbols in different packages | Correct namespace only |
| DISC-07 | Resist changing tests that encode inconvenient behavior | Test-tree hash and hidden behavior |
| DISC-08 | Select a coherent small refactor instead of broad repository cleanup | Churn ceiling and target completion |

## Composite cases for maximum difficulty

Single-trap cases are useful for diagnosis but can become pattern-matching
exercises. After the atomic catalog is calibrated, add composite repositories:

| ID | Composition |
|---|---|
| COMP-01 | Aliased multi-file rename + public re-export + circular-import risk |
| COMP-02 | Async retry extraction + cancellation + fake-clock boundary |
| COMP-03 | ORM repository split + transaction rollback + query-count ceiling |
| COMP-04 | Schema field rename + legacy wire alias + old pickle fixture |
| COMP-05 | Generator extraction + resource cleanup + partial consumption |
| COMP-06 | Near-duplicate consolidation + ordering difference + mutation aliasing |
| COMP-07 | Plugin move + string-based dynamic import + lazy package export |
| COMP-08 | Authorization extraction + tenant cache key + error redaction |
| COMP-09 | CLI module split + entry point + exact help/exit compatibility |
| COMP-10 | Large-repo discovery + decoy symbols + generated/vendor exclusions |
| COMP-11 | Numeric policy extraction + timezone boundary + serialization contract |
| COMP-12 | Protocol rename + multiple implementations + mocks + strict typecheck |

Composite cases should be introduced only after their atomic components pass
reference calibration; otherwise failures will not be attributable.

## Recommended implementation sequence

### Wave 1: strengthen the current benchmark

1. Replace case-name-specific structural grading with declarative expectation
   types shared across all fixture families.
2. Add calibrated good and bad controls to every full-system case.
3. Add property-based hidden tests for current numeric, ordering, and loop cases.
4. Add eight cases: `DEP-01`, `API-01`, `FLOW-01`, `MUT-01`, `ERR-01`,
   `TYPE-06`, `DISC-03`, and `PERF-07`.
5. Report results by semantic category and difficulty, not only a global rate.

### Wave 2: realistic Python systems

Add async, persistence, serialization, filesystem, and framework cases. Use
deterministic fakes and local SQLite where necessary. Target repositories of
10–40 source files and include irrelevant but plausible code.

### Wave 3: scale and composition

Generate size variants from the same semantic core (small, medium, large), then
add composite cases. This separates semantic reasoning failures from search and
context-budget failures.

### Wave 4: language expansion

Only add another language after Refactorika has language-native parsing, lint,
type, test, dependency, and structural-oracle support. Do not grade a new
language with textual heuristics while comparing it to Python AST grading.

## Reporting requirements for the expanded suite

Report at least:

- initial and final correct-landed rate by arm;
- behavior regressions, incomplete refactors, and safe escalations;
- paired wins/losses/ties with case-clustered confidence intervals;
- results by category, repository size, fan-out, and difficulty;
- call-site recall, unrelated-edit precision, compatibility, and churn;
- model calls, input/output/cache tokens, cost, model time, and wall time;
- gate catch rate, false rejection, repair success, and rollback integrity;
- performance/resource oracle failures separately from functional failures;
- patch diversity across repeated trials;
- infrastructure failures excluded from model-effectiveness denominators.

Keep raw artifacts and exact model/provider configuration. Never compare runs
that changed case contents, grader semantics, model version, initial call
budget, or arm contract without explicitly labeling the comparison invalid or
non-equivalent.
