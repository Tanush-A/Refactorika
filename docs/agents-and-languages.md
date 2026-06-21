# Agent campaign & language adapters

Two subsystems present on **both branches** (with one wiring difference). See [branches.md](branches.md).

---

## The agent campaign (`refactorika/agents/`)

A **specialist agent** brings *judgment* for one refactor kind; deterministic engines + the gate stack
bring *correctness*. The campaign flow: **audit → plan → confirm → dispatch to specialists →
verified apply**.

### Flow

1. `analysis/audit.py:build_plan(path, storage)` — `audit_repo` finds per-file opportunities, ranks
   them, picks a `dominant_finding`; then orders deviating files **fewest-dependents-first** (lowest
   blast radius) via `CallGraph`, producing a `Plan` of `PlanTask`s. Persisted to storage.
2. Confirm — `working` exposes `confirm_plan(decision, order)` (approve/reject/reorder, never edits
   code); `main`'s `run_campaign` auto-confirms.
3. Dispatch — `agents/orchestrator.py:dispatch_plan`:
   - **`working`:** runs tasks in **parallel waves** grouped by `order` (`ThreadPoolExecutor`,
     `max_workers`), verifying each via `core/apply.py:apply_and_verify`.
   - **`main`:** **single-threaded**, rebuilds the Jedi graph before each task, verifies via
     `pipeline/checker.py` (impact-scoped tests).
   - Routes each task to the specialist whose `supported_kinds` covers the task's dominant kind. One
     agent raising doesn't sink the campaign.

### `SpecialistAgent` base (`agents/base.py`)

`handle(task, storage, …)` → `EditRecord`. Two proposal paths:
- `propose_specs(...)` → `list[TransformSpec]` (preferred; `main` only, routed through the deterministic
  engine + checker), or
- `propose(task, storage)` → full new file contents (the legacy text path, verified via
  `apply_and_verify`). Default `propose` is a no-op (returns the file unchanged).

### The specialists

| Agent | `supported_kinds` | Status |
|---|---|---|
| `ImportAgent` | `reorder_imports` | **Live**, deterministic — `transforms/imports.py:reorder_imports` (stdlib → third-party → local, dedup). |
| `DeadCodeAgent` | `remove_dead_code` | **Live**, deterministic — `find_dead_code` → `transforms/dead.py:remove_dead_symbols` (high-confidence only). |
| `ComplexityAgent` | `split_function`, `flatten_nesting`, `extract_helper`, `split_module`, `dedupe_block`, (`decompose_function`) | **`main`: live** (LLM decomposition via the planner + decision memory, through the deterministic engine). **`working`: stub** (returns the file unchanged — no LLM wired). |
| `DuplicateAgent` | `consolidate_duplicate` | **Stub on both** — placeholder for multi-file consolidation via `apply_and_verify_multi`; on the roadmap. |

So on **`working`** the campaign reliably does **import reordering + high-confidence dead-code
removal**, verified; complexity/duplicate are stubs. On **`main`** complexity decomposition is also live.

---

## Language adapters (`refactorika/languages/`)

A registry that dispatches the per-language gate primitives (parse / lint baseline+gate / typecheck
baseline+gate / file collection). `core/apply.py` and `harness.py` call `detect_language(path)` per
file, so the gate stack is language-aware.

- `base.py:LanguageAdapter` — abstract: `parse_gate`, `lint_baseline`, `lint_gate`,
  `typecheck_baseline`, `typecheck_gate`, `collect_files`. Defaults skip (return `None`).
- `registry.py` — `register_adapter(adapter, generic=False)` and `detect_language(path)` (by extension,
  else the generic fallback).
- `python_adapter.py:PythonAdapter` (`.py`) — the only fully-implemented adapter; delegates all gates to
  `core/gates.py` (tree-sitter parse, ruff, pyright) and collects `.py` files via `rglob`.
- `generic_adapter.py:GenericAdapter` — fallback for unknown extensions: **all gates skip** (so a
  multi-language repo degrades gracefully; the test suite still runs), accepts a single file only.
- `__init__.py` registers Python + the generic fallback, and *tries* to register a TypeScript adapter —
  but **`typescript_adapter.py` does not exist**, so that import always fails silently. It's an
  extensibility hook, not a working adapter.

**Multi-language status:** the registry pattern is real and ready; **only Python is implemented**.
Multi-language is explicitly deferred/out of scope. (Note: the project's older `CLAUDE.md` lists
multi-language as parked — the adapter scaffolding is the readiness for it, not a contradiction.)
