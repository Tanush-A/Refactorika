# Refactorika

An MCP server that gives Claude **verified** structural-refactoring powers over a Python codebase.
Claude proposes the refactored code; Refactorika runs every edit through a gate stack
(**parse → ruff → pyright → pytest**) and **commits only what passes — rolling back anything that
breaks behavior**. The pitch: *the agent restructured it, but nothing landed unverified.*

## Golden path
`analyze → propose → apply → verify → commit`

- **`analyze_file(path)`** — ranked structural smells (file size, import order/dupes, function
  length, nesting depth).
- **`apply_and_verify(path, new_content, refactor_kind)`** — atomic. Snapshot → write → gate stack
  (cheapest first, short-circuit on fail) → **commit if green / roll back if not** → append an
  `EditRecord`. On `rolled-back`, read `failure_reason` and re-propose.
- **`get_log()`** — the append-only edit log (powers the dashboard).

Skipped gates (tool missing / no covering test) are recorded as `null`, **never silent-passed**.
State persists to Redis when reachable, else a local JSON file (`.refactorika/state.json`).

## Quickstart
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

# 30-second demo: analyze a messy file, commit a good refactor,
# watch a type-clean but behavior-breaking edit get caught by pytest and rolled back.
git -C demo_repo init -q && git -C demo_repo add -A && git -C demo_repo commit -qm "initial"
PATH=.venv/bin:$PATH .venv/bin/python -m scripts.demo

# run the test suite
PATH=.venv/bin:$PATH .venv/bin/python -m pytest -q
```

## Run as an MCP server
```bash
PATH=.venv/bin:$PATH .venv/bin/refactorika   # stdio MCP server; register with Claude Code
```

## Layout
```
refactorika/core/   schema · analyze · gates · apply · storage   (interface-agnostic core)
refactorika/        mcp_server (thin shell) · dashboard
demo_repo/          curated messy target repo + tests
eval/agents/        shared loop · providers · tools · harness tools · campaign
tests/              unit tests
```

Scope is deliberately narrow (v1): simple Python codebases, behavior-preserving refactors only.
See `CLAUDE.md` for the full project memory and `docs/` for problem/scope/stack detail.

## Benchmarks and testing

Refactorika has three different kinds of validation. They answer different
questions and their results should not be pooled:

| Validation | Command | Question answered |
|---|---|---|
| Unit and integration tests | `make test` | Does the implementation behave as expected? |
| Full-system benchmark | `make benchmark-full-agent` | Can an agent discover and land a correct refactor from an underspecified request? |
| Shared-patch ablation | `make benchmark-agent` | Does verification, rollback, and repair improve the outcome of the same initial patch? |

The full-system benchmark is the primary product benchmark. Every agent begins
with exactly:

```text
refactor this codebase
```

The hidden tests and structural targets are held by the evaluator. They are not
included in the request, repository snapshot, harness context, or repair
feedback.

### Install the benchmark environment

The benchmark uses a dedicated environment because its gate stack needs Ruff,
Pyright, and Pytest:

```bash
make setup
```

For Anthropic model runs, put the key in the process environment or in an
uncommitted `.env` file:

```bash
ANTHROPIC_API_KEY=... \
  eval/.venv/bin/python -m eval.full_system_bench --help
```

Do not commit `.env` or benchmark result artifacts containing model prompts and
patches.

### Recommended testing sequence

Run these in order when changing the harness, benchmark runner, or fixtures:

```bash
# 1. Test the implementation and benchmark plumbing.
make test

# 2. Prove that every fixture starts behaviorally correct but still contains
#    its intended refactoring opportunity. This makes no model API calls.
make benchmark-full-calibrate

# 3. Run a cheap, single-case live pilot before spending on the complete suite.
PATH="$PWD/eval/.venv/bin:$PATH" eval/.venv/bin/python \
  -m eval.full_system_bench \
  --provider anthropic \
  --model claude-sonnet-4-5-20250929 \
  --case guard_clause_continue \
  --trials 1

# 4. Run the complete 49-case OFF/ON comparison.
TRIALS=3 MODEL=claude-sonnet-4-5-20250929 make benchmark-full-agent
```

Calibration must be valid before interpreting a live run. A failed calibration
means the fixture or grader is broken; it is not a model result.

### Full-system benchmark design

For every `(case, trial)`, the runner creates a fresh repository copy for each
arm. Patches from one arm cannot affect another arm. OFF and ON form the default
paired experiment; the two loop-based arms are optional.

| Arm | Enabled by | Repository interaction | Planning and mutation policy |
|---|---|---|---|
| `off` | Always | One prompt containing the visible repository snapshot | The model independently chooses a refactor and returns complete changed Python files in one call. The patch is written without Refactorika gates. |
| `on` | Always | Refactorika audit/plan context plus the visible snapshot | The model independently proposes a patch. Refactorika applies parse, Ruff, Pyright, and visible-Pytest gates atomically, rolls back failures, and supplies diagnostics for bounded repair. |
| `agentic` | `--agentic` | Shared bounded file, glob, ranged-read, batch-read, search, references, Git, test, lint, typecheck, and multi-file patch tools | The shared state machine enforces discovery, selection, planning, execution, verification, bounded repair, and completion auditing. Patches apply directly and campaigns roll back when incomplete. |
| `agentic+harness` | `--agentic-mcp` | The identical developer-tool schema used by `agentic` | Refactorika preloads repository audit/plan/context and routes the same patch shape through atomic multi-file verification and structured rollback. The CLI flag retains its earlier `mcp` name. |

OFF and ON are independent proposals, not the same patch. This measures the
whole workflow—including Refactorika's discovery, context, verification, and
repair—not merely whether gates can reject a known bad edit.

The optional loop comparison is useful when the desired baseline is a coding
agent rather than a one-call completion. To run both loop arms alongside the
default pair using the same Sonnet model:

```bash
PATH="$PWD/eval/.venv/bin:$PATH" eval/.venv/bin/python \
  -m eval.full_system_bench \
  --provider anthropic \
  --model claude-sonnet-4-5-20250929 \
  --agentic \
  --agentic-model claude-sonnet-4-5-20250929 \
  --agentic-mcp \
  --agentic-mcp-model claude-sonnet-4-5-20250929 \
  --agentic-max-iter 30 \
  --agentic-mcp-max-iter 30 \
  --case guard_clause_continue \
  --trials 1
```

Remove `--case guard_clause_continue` only after the pilot succeeds. A complete
49-case, three-trial, four-arm run can make hundreds of model calls, especially
when the loop arms use their full iteration budgets.

#### Timeouts and error monitoring

Every blocking operation is bounded so a stalled model or test process cannot
wait indefinitely:

| Control | Default | Applies to |
|---|---:|---|
| `--request-timeout` | 180 seconds | Each remote model request in every arm |
| `--agent-timeout` | 900 seconds | Total wall-clock budget for each optional tool-loop agent |
| `--shell-timeout` | 30 seconds | Each shell command issued by a loop agent |
| `--gate-timeout` | 180 seconds | Each Ruff, Pyright, or Pytest gate subprocess |
| `--agentic-max-iter` | 30 calls | Maximum control-agent model turns |
| `--agentic-mcp-max-iter` | 30 calls | Maximum harness-agent model turns |

For a fast smoke run, reduce the budgets explicitly:

```bash
PATH="$PWD/eval/.venv/bin:$PATH" eval/.venv/bin/python \
  -m eval.full_system_bench \
  --case guard_clause_continue \
  --trials 1 \
  --request-timeout 60 \
  --agent-timeout 300 \
  --shell-timeout 20 \
  --gate-timeout 90
```

Provider failures, malformed responses, loop timeouts, iteration exhaustion,
gate crashes, invalid patches, and configuration failures are recorded
separately. Timeout, gate, or agent infrastructure failures make the run
`invalid-infrastructure`; they are not counted as model correctness failures.
Unexpected exceptions also produce
an `invalid-infrastructure` JSON artifact containing the exception class and
message, and are sent to Sentry when `SENTRY_DSN` is configured. Sentry events
are scrubbed of prompts, patches, source, paths, local variables, and secrets.

The shared-patch benchmark accepts `--request-timeout` as well. Its model and
oracle-test calls default to 180 seconds.

All benchmark CLI commands emit timestamped, flushed progress lines to stderr.
Full-system runs report every case, arm, proposal attempt, fallback, completion
status, model-call count, and elapsed time. Shared-patch calibration reports all
50 controls individually, and live runs report each task and arm. JSON remains
on stdout and in the result artifact, so progress messages do not corrupt it.
Pass `--quiet-progress` to either runner to suppress these messages. `make test`
uses verbose Pytest output and prints every test name as it runs.

#### Parallel arm execution

Add `--parallel-arms` to start the initial OFF, ON, agentic, and
agentic+harness agents concurrently. Every arm still receives its own isolated
repository. ON repairs remain sequential after its initial proposal because
each repair depends on the preceding gate diagnostics.

```bash
PATH="$PWD/eval/.venv/bin:$PATH" eval/.venv/bin/python \
  -m eval.full_system_bench \
  --agentic \
  --agentic-mcp \
  --parallel-arms \
  --parallel-fallback-delay 2 \
  --case guard_clause_continue \
  --trials 1
```

The Make target exposes the same mode:

```bash
AGENTIC=1 AGENTIC_MCP=1 PARALLEL_ARMS=1 TRIALS=1 \
  make benchmark-full-agent
```

After every concurrent call finishes, only arms that encountered a provider,
timeout, agent-loop, or gate infrastructure failure are reset as necessary and
retried once sequentially. Successful arms are not repeated. The cooldown
before those retries defaults to two seconds and is controlled by
`--parallel-fallback-delay`. Configuration errors, malformed model output, and
iteration-budget exhaustion are not retried because serialization does not make
them recoverable.

Each record includes `execution.parallel_arms`, `sequential_fallback`,
`fallback_arms`, and `fallback_delay_seconds`. Parallel execution reduces wall
time but can introduce provider throttling and local CPU contention, so use the
default sequential mode when comparing fine-grained wall-clock timing.

For an OpenAI-compatible local server, the default OFF/ON pair supports:

```bash
PROVIDER=openai \
MODEL=your-model \
BASE_URL=http://localhost:11434/v1 \
TRIALS=1 \
make benchmark-full-agent
```

The current loop backends call Anthropic's Messages API directly and therefore
still require `ANTHROPIC_API_KEY`; `--provider openai` applies only to the
default OFF/ON backend.

### What each full-system case contains

Each case is a small, isolated Python repository with:

- agent-visible source, configuration, documentation, and sometimes visible
  tests;
- at least one intended behavior-preserving structural improvement;
- hidden Pytest tests that exercise semantic traps not fully covered visibly;
- structural expectations that verify the refactor actually happened;
- expected and allowed paths used for scope and call-site metrics.

The final grader injects hidden tests only after the agent finishes. A result is
`correct_landed` only when the arm made an effective source change, landed or
shipped it, passed visible and hidden behavior, and satisfied the structural
target. Consequently, merely making the tests pass is insufficient.

The current 49 cases are grouped as follows.

#### Foundational behavior cases (3)

| Case | Refactoring challenge | Main trap |
|---|---|---|
| `rounding_order` | Consolidate validation and price calculation | Integer discount/tax ordering changes boundary values. |
| `guard_clause_continue` | Flatten a nested event-filtering loop | `return` exits the collection while `continue` skips one record. |
| `near_duplicate_semantics` | Extract shared account filtering | Trial and paid flows differ at expiry equality and output ordering. |

#### Foundational multi-file cases (3)

| Case | Refactoring challenge | Main trap |
|---|---|---|
| `rename_with_call_sites_and_reexport` | Rename an internal symbol | Attribute callers or the compatibility export can be missed. |
| `move_symbol_and_update_imports` | Move formatting between modules | Different import styles must both follow the move. |
| `internal_rename_preserves_public_api` | Adopt an internal naming convention | Existing package consumers must retain the legacy public API. |

#### Foundational recovery cases (3)

| Case | Refactoring challenge | Main trap |
|---|---|---|
| `type_clean_threshold_regression` | Extract shipping-fee policy | `>=` versus `>` silently changes the free-shipping boundary. |
| `nullable_return_requires_targeted_repair` | Refactor inventory availability | An accidental nullable return breaks the caller contract. |
| `repeated_invalid_repairs_escalate` | Complete a syntactically valid refactor | Repeated invalid proposals must roll back and eventually escalate. |

#### Core stress cases (8)

`aliased_qualified_multifile_rename`, `keyword_compatible_api_rename`,
`nested_loop_break_scope`, `normalization_preserves_input_ownership`,
`exception_translation_preserves_cause`, `none_vs_missing_sentinel`,
`generated_vendor_decoy_unchanged`, and `stable_sort_tie_order` cover aliased
call sites, keyword compatibility, nested control flow, mutation ownership,
exception chaining, sentinel semantics, forbidden generated-code edits, and
stable ordering.

#### Repository-scale cases (2)

`scale_20_file_rename_move` and `scale_100_file_rename_move` present the same
high-fanout normalization move inside deterministic repositories of different
sizes. They include direct, aliased, qualified, keyword, re-export, registry,
legacy-compatibility, and generated-file traps plus realistic unrelated code.

#### Additional semantic stress cases (10)

`numeric_threshold_inclusivity`, `integer_rounding_sequence`,
`loop_guard_continue_scope`, `nested_alias_ownership`,
`key_error_payload_identity`, `domain_error_chain_context`,
`generator_close_cleanup`, `recursive_cycle_identity`,
`first_seen_group_order`, and `cleanup_does_not_mask_return` cover boundaries,
rounding, loop scope, aliasing, exact error contracts, generators, recursion,
ordering, and cleanup behavior.

#### Contract and topology stress cases (10)

`extra_alias_and_qualified_imports`, `extra_circular_import_sensitive_move`,
`extra_package_export_contract`, `extra_keyword_signature_compatibility`,
`extra_dynamic_plugin_path`, `extra_dataclass_contract`,
`extra_protocol_call_sites`, `extra_generated_vendor_exclusion`,
`extra_decoy_same_named_symbol`, and `extra_enum_value_contract` cover import
topology, circular dependencies, exports, signatures, dynamic lookup,
dataclass/protocol contracts, decoys, generated code, and enum identity.

#### Systems stress cases (10)

`async_cancellation_releases_lease`, `async_gather_preserves_input_order`,
`transaction_failure_restores_snapshot`,
`serialization_distinguishes_null_and_missing`,
`serialization_preserves_timezone_offsets`,
`serialization_uses_enum_wire_values`,
`filesystem_atomic_write_cleans_temporary_file`,
`filesystem_root_confinement_survives_refactor`,
`middleware_wrapping_order_is_stable`, and
`cache_reuses_load_and_closes_source` cover asynchronous cancellation and
ordering, rollback, wire formats, timezone preservation, filesystem safety,
middleware order, caching, and resource cleanup.

Use `--case` more than once to run a focused subset:

```bash
PATH="$PWD/eval/.venv/bin:$PATH" eval/.venv/bin/python \
  -m eval.full_system_bench \
  --case rounding_order \
  --case move_symbol_and_update_imports \
  --trials 1
```

### Full-system grading and metrics

Every record contains the final categorical outcome:

| Field | Meaning |
|---|---|
| `correct_landed` | An effective refactor landed, behavior passed, and the structural target passed. |
| `regression_shipped` | The arm shipped an edit that failed behavioral grading. |
| `incomplete_refactor_shipped` | Behavior passed, but the requested structural effect was incomplete. |
| `status` | `shipped`, `committed`, `skipped-needs-human`, or `error`, depending on arm policy. |
| `oracle_pass` | Visible plus held-out behavioral result; `null` when nothing was committed for grading. |
| `structural_failures` | Machine-readable reasons the intended structural change was incomplete. |

Aggregate output reports:

- micro and case-macro correct-landed rates;
- paired wins, losses, ties, and case-clustered bootstrap confidence intervals;
- regressions shipped, incomplete refactors, and safe escalations;
- initial rejections, bad proposals caught, false rejections, repair success,
  rejection gates, and rollback integrity;
- model calls, input/output/cache tokens, configured dollar cost, and timing;
- changed-path recall, unrelated-edit precision, churn, missed call sites,
  compatibility, and patch diversity;
- configuration, provider, malformed-response, and invalid-patch failures.

Initial and final ON results are intentionally separate. The initial result
measures proposal quality; the final result includes the benefit and cost of
gate-guided repair.

Results default to:

```text
eval/results/full-system-latest.json
```

Choose a different artifact path with `--output`. Configure cost reporting with
per-million-token rates:

```bash
INPUT_COST_PER_MTOK=3 \
OUTPUT_COST_PER_MTOK=15 \
CACHE_READ_COST_PER_MTOK=0.30 \
CACHE_WRITE_COST_PER_MTOK=3.75 \
TRIALS=3 \
make benchmark-full-agent
```

Passing a prior artifact through `--baseline` enables a sanitized Sentry warning
for a material ON-arm correctness regression. Sentry is errors-only and enabled
only when `SENTRY_DSN` is configured.

### Shared-patch verification ablation

The narrower ablation asks a different causal question. For each task, both
arms share the exact same initial model patch:

- OFF writes the patch directly.
- ON routes it through parse, Ruff, Pyright, visible Pytest, rollback, and up to
  two diagnostic-guided repairs.
- Hidden oracle tests are injected only for final grading.

Its ten tasks—`withdraw`, `reserve`, `discount`, `parse_port`, `page_end`,
`retry_delay`, `quota_left`, `shipping`, `score`, and `batch_count`—convert a
service API from raising `ValueError` to returning a result value while updating
its caller.

Before a model run, calibrate 50 controls: one known-good and four known-bad
patches for each task. The bad controls cover visible behavior, missed callers,
syntax/type defects, and held-out-only boundary errors:

```bash
make benchmark
TRIALS=3 MODEL=claude-sonnet-4-5-20250929 make benchmark-agent
```

Results default to `eval/results/harness-latest.json`. Calibration results prove
the evaluator recognizes its controls; they must not be reported as model
performance.

### Benchmark implementation tests

The normal test suite includes runner and fixture tests for:

- hidden-oracle isolation and case normalization;
- independent OFF and ON proposals;
- baseline calibration and behavior/structure separation;
- gate attribution, retry accounting, rollback integrity, and escalation;
- pricing, token/timing fields, and infrastructure-failure handling;
- the 49-case registry, including deterministic 20- and 100-file scale cases,
  and declarative stress-case grading.

Run only benchmark-related tests with:

```bash
PATH="$PWD/eval/.venv/bin:$PATH" eval/.venv/bin/python -m pytest -q \
  tests/test_full_system_bench.py \
  tests/test_full_system_case_registry.py \
  tests/test_full_system_behavior_cases.py \
  tests/test_full_system_multifile_cases.py \
  tests/test_full_system_recovery_cases.py \
  tests/test_stress_cases.py \
  tests/test_harness_tasks.py
```

### Interpretation limits

- These are controlled, project-owned Python repositories, not a representative
  sample of production repositories. Report raw counts and multiple trials.
- Do not combine full-system and shared-patch results; they use different
  interventions and experimental units.
- The optional `agentic+harness` arm currently calls Refactorika's Python APIs
  in-process. It does not measure MCP transport, server startup, serialization,
  or protocol failures. The `--agentic-mcp` CLI flag is a historical name.
- Plans are model-produced from repository evidence. Their quality is measured;
  hidden structural expectations are never copied into the plan.
- Tool traces, rollback state, phase tokens, plan completion, termination reason,
  and completion-audit outcomes are recorded, but gate execution time remains
  included in overall loop time rather than isolated perfectly.
- A small number of trials cannot establish statistical significance. Prefer
  case-level outcomes and the clustered interval over one aggregate percentage.

More detail is available in [eval/README.md](eval/README.md), the
[full-system experimental contract](docs/13-full-system-benchmark.md), and the
[case catalog and stress plan](docs/14-benchmark-case-catalog-and-stress-plan.md).
The primary loop/non-loop comparison is frozen in the
[four-arm agent contract](docs/15-four-arm-agent-benchmark-contract.md).
