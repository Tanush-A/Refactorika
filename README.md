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
tests/              unit tests
```

Scope is deliberately narrow (v1): simple Python codebases, behavior-preserving refactors only.
See `CLAUDE.md` for the full project memory and `docs/` for problem/scope/stack detail.

## Benchmarks

Run the full-system benchmark in which both independent agents receive only
`refactor this codebase` as the initial user request:

```bash
make benchmark-full-calibrate
MODEL=claude-sonnet-4-5-20250929 make benchmark-full-agent
```

The older shared-patch verification ablation remains available separately:

```bash
make benchmark
```

See [eval/README.md](eval/README.md) for the methodology and result fields.
