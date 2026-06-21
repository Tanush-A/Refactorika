# Architecture

Refactorika is **one interface-agnostic core library** wrapped in a **thin MCP shell**. The core holds all logic — analysis, the gate stack, transforms, Redis Iris memory — and reads/writes state itself, so every entry point (the MCP server, the demo script, the tests) sees the same thing. Claude proposes; the core verifies and remembers.

## Project Layout

The canonical package is the top-level **`refactorika/`**. Modules marked *(exists)* are shipped today; *(to build)* are on the build order in [02-scope.md](02-scope.md).

```
Refactorika/
├── pyproject.toml                  # package metadata + dependencies
├── refactorika/                    # ← the package (interface-agnostic core + thin shell)
│   ├── __init__.py
│   ├── mcp_server.py               # FastMCP shell: registers every tool          (exists)
│   ├── dashboard.py                # renders the edit log — visible verification   (exists)
│   ├── core/
│   │   ├── schema.py               # frozen contracts: Opportunity, EditRecord…    (exists)
│   │   ├── analyze.py              # structure analysis → ranked opportunities     (exists)
│   │   ├── apply.py                # apply_and_verify: the atomic mutation heart   (exists)
│   │   ├── gates.py                # parse → ruff → pyright → pytest               (exists)
│   │   └── storage.py              # Redis Iris client + local-JSON fallback       (exists)
│   ├── analysis/
│   │   ├── parser.py               # shared tree-sitter front end                  (to build)
│   │   ├── duplicates.py           # structural fingerprint + semantic pairing     (to build)
│   │   ├── dead_code.py            # call-graph reachability + confidence          (to build)
│   │   ├── embeddings.py           # local/OpenAI embedding generation             (to build)
│   │   └── call_graph.py           # directed symbol graph builder                 (to build)
│   ├── memory/                     # Redis Iris wrappers (generalize storage.py)
│   │   ├── agent_memory.py         # cross-session module context + history        (to build)
│   │   ├── vector_index.py         # per-function embeddings + cosine queries       (to build)
│   │   └── context.py              # Context Retriever (structured + vector)        (to build)
│   └── docs_gen.py                 # generate_docs / context-map emission          (to build)
├── demo_repo/                      # curated messy target repo + its tests         (exists)
├── scripts/demo.py                 # scripted golden-path walkthrough              (exists)
└── tests/                          # unit tests                                    (exists)
```

> **Note — one tree only.** An earlier skeleton lived under `src/refactorika/` (`server.py`, `tools/*.py` stubs). That tree is **abandoned and slated for deletion**; do not add to it. All code lives under top-level `refactorika/`, and `pyproject.toml` should package that path.

## MCP Framework

Refactorika uses the **`mcp` Python SDK** with `FastMCP`. The shell wraps the core 1:1 — each tool is a thin function that calls a core entrypoint and returns a JSON-serializable result.

```python
# refactorika/mcp_server.py  (excerpt — exists today)
from mcp.server.fastmcp import FastMCP
from .core.analyze import analyze_file as _analyze_file
from .core.apply import apply_and_verify as _apply_and_verify
from .core.storage import Storage

mcp = FastMCP("refactorika")
_storage = Storage()

@mcp.tool()
def analyze_file(path: str) -> dict:
    """Ranked structural-refactor opportunities for a Python file (read-only)."""
    return _analyze_file(path, _storage).to_dict()
```

## The two tool classes

Everything the harness exposes is either **advisory** (read-only — finds and explains) or a **verified mutation** (gated — changes code only if proven safe). Advisory output feeds Claude's reasoning; Claude then proposes a concrete edit that goes through the single mutation entrypoint.

### Advisory tools (read-only, never mutate)

| Tool | Description | Status |
|---|---|---|
| `analyze_file(path)` | Ranked structural smells: file size, import order/dupes, function length, nesting depth | exists |
| `find_duplicates(path)` | Structural fingerprint + semantic vector search; ranked pairs of duplicate functions with a consolidation target | to build |
| `find_dead_code(path)` | Call-graph reachability; unreachable symbols with a confidence score | to build |
| `generate_docs(path)` | Generate/update `.refactorika/context/<module>.md` (purpose, exports, dependents, decisions) | to build |
| `get_context_map(path)` | Module context summary pulled from Redis agent memory | to build |
| `get_log()` | The append-only edit log (powers the dashboard) | exists |

### Verified mutation (single atomic entrypoint, gated)

| Tool | Description | Status |
|---|---|---|
| `apply_and_verify(path, new_content, refactor_kind)` | Apply Claude's proposed file contents, run the gate stack, **commit on green / roll back on fail**, append an `EditRecord` | exists |
| `apply_and_verify_multi(edits, refactor_kind)` | Same, atomically across **multiple files** (needed for cross-file duplicate merges) | to build |

`refactor_kind` covers every organization/complexity edit **and** `consolidate_duplicate` / `remove_dead_code` — duplicate merges and dead-code deletions are nothing special; they are ordinary mutations that must pass `pytest` like any other. That is how "find dead code" becomes "**safely remove** dead code."

## Data flow — the harness loop

```
Claude (reasoning agent)
   │  1. analyze_file / find_duplicates / find_dead_code / get_context_map   ← ADVISORY (read-only)
   │        └── core: tree-sitter parse, call graph, embeddings
   │             └── Redis Iris: AST cache, vector index, agent memory, context retriever
   │  2. Claude proposes concrete new file contents
   │  3. apply_and_verify(path, new_content, kind)                           ← VERIFIED MUTATION
   │        └── snapshot → parse → ruff → pyright → pytest
   │             ├── all green → git commit, append EditRecord(committed)
   │             └── any fail  → restore file, append EditRecord(rolled-back, reason)
   │  4. on rollback, Claude reads failure_reason and re-proposes
   └── 5. generate_docs / get_log → living docs + the dashboard trail; refactor history → agent memory
```

## Verification gates — cheapest-first, short-circuit on fail

1. **Parse** — `tree-sitter-python` must parse the edited content (no `ERROR`/`MISSING`). Runs before touching disk.
2. **Lint/format** — `ruff format` to normalize, then reject only *new* violations vs. the pre-edit baseline.
3. **Type** — `pyright`; zero errors or roll back.
4. **Behavior** — `pytest` over tests covering the touched file. Roll back on fail; record a **skip** (never a silent pass) where no test covers the file.
5. **Re-propose** — bounded retries; the failure reason is surfaced back to Claude.
6. **Escalate** — retries exhausted → mark `skipped-needs-human`, revert to last good state, flag it. Never force-commit.
7. **Log** — append the structured `EditRecord` (powers the dashboard; written to agent memory).

Each gate returns `True` (pass), `False` (fail → roll back), or `None` (skipped and recorded — tool missing / no coverage). **Skips are explicit, never silent passes** — honest coverage is part of the trust story.

## Edit-log schema (frozen contract)

```
{ file, files, refactor_kind, checks: { parse, lint, typecheck, tests }, retries, status, failure_reason, diff }
status ∈ { committed, rolled-back, skipped-needs-human }
```

`files` is the list of all touched paths (multi-file edits); `file` stays as the first/primary for back-compat.

## Running the server

```bash
# stdio transport for Claude Code / Claude Desktop
python -m refactorika.mcp_server

# scripted golden-path demo (analyze → good refactor commits → bad edit caught + rolled back → dashboard)
python -m scripts.demo
```

## Testing

```bash
pytest tests/                 # run all tests
pyright refactorika/          # type check the package
ruff check refactorika/       # lint
```
</content>
