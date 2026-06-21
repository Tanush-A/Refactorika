# Front doors: CLI and MCP

The exact command-line and MCP surface, for **both branches**. The two branches differ here more than
anywhere else, so each is documented separately. See [branches.md](branches.md) for why.

---

## `working` branch (the demo)

On `working`, the **MCP server is the primary front door** and a small `scan`/`fix` CLI plus a
scripted demo round it out.

### Console entrypoints

`pyproject.toml` declares two identical entrypoints (both → `refactorika.cli:main`):

```
refactorika        = refactorika.cli:main
refactorika-scan   = refactorika.cli:main
```

### CLI (`refactorika/cli.py`)

```
refactorika serve                  # default when no subcommand: start the MCP server on stdio
refactorika scan <path> [opts]     # advisory: print ranked refactor opportunities, no writes
refactorika fix  <path> [opts]     # apply mechanical, verified fixes in place
```

**`scan <path>`** — read-only structural report. Sections: structural opportunities (ranked),
duplicate functions, dead code (by confidence), and module context ("living docs"). Options:

| Option | Effect |
|---|---|
| `--no-dupes` | skip duplicate detection |
| `--no-dead` | skip dead-code detection |
| `--no-docs` | skip module-context generation |

**`fix <path>`** — auto-apply mechanical fixes through the gate stack (`parse → ruff → pyright →
pytest`), committing only what passes. Options:

| Option | Default | Effect |
|---|---|---|
| `--dry-run` | off | show what would change; write nothing |
| `--kinds a,b` | `imports,dead` | which fix kinds to apply: `imports` (reorder/dedupe) and/or `dead` (remove high-confidence dead code) |
| `--multi-agent` | off | dispatch the confirmed plan through the parallel specialist agents instead of the inline fixers |

Output per file shows the gate results (`parse`, `lint`, `typecheck`, `tests`) and a
`committed | rolled-back | skipped` summary.

### MCP server (`refactorika/mcp_server.py`) — 13 tools

Start it with `refactorika serve` (or just `refactorika`). It is a FastMCP stdio server named
`refactorika`. Register it with Claude Code, e.g.:

```bash
claude mcp add refactorika -- uvx refactorika serve   # or: .venv/bin/refactorika serve
```

| Tool | Kind | Signature | Returns |
|---|---|---|---|
| `analyze_file` | advisory | `analyze_file(path)` | ranked structural opportunities for one file |
| `find_duplicates` | advisory | `find_duplicates(path, threshold=0.55)` | structural + semantic duplicate pairs with consolidation targets |
| `find_related` | advisory | `find_related(path, symbol="", k=5)` | semantically similar functions elsewhere + call-graph dependents (impact check) |
| `find_dead_code` | advisory | `find_dead_code(path)` | unreachable symbols ranked by confidence |
| `generate_docs` | advisory | `generate_docs(path)` | module context; persists to memory + `.refactorika/context/<module>.md` |
| `get_context_map` | advisory | `get_context_map(path)` | accumulated cross-session context for a module |
| `audit_repo` | advisory | `audit_repo(path)` | repo-wide ranked opportunity report (dominant finding) |
| `get_plan` | planning | `get_plan(path)` | dependency-ordered plan (fewest-dependents-first); persists it for `confirm_plan` |
| `confirm_plan` | planning | `confirm_plan(decision="approve", order=None)` | human checkpoint: approve / reject / reorder the persisted plan. **Never edits code.** |
| `apply_and_verify` | **verified mutation** | `apply_and_verify(path, new_content, refactor_kind)` | atomic write → gate stack → commit or roll back → `EditRecord` |
| `apply_and_verify_multi` | **verified mutation** | `apply_and_verify_multi(edits, refactor_kind)` | multi-file atomic apply (snapshot all → gates once → one commit or restore all) |
| `run_agents` | execution | `run_agents(max_workers=4)` | dispatch the **confirmed** plan to specialist agents in parallel waves; run summary |
| `get_log` | observability | `get_log()` | the append-only edit log (powers the dashboard) |

**The golden path** the harness is built around: `analyze → propose → apply_and_verify → commit`.
Claude calls an advisory tool, writes new file contents, and submits them through
`apply_and_verify`; on `rolled-back` it reads `failure_reason` and re-proposes. Skipped gates are
recorded as `null`, never silently passed.

### The scripted demo

```bash
git -C demo_repo init -q && git -C demo_repo add -A && git -C demo_repo commit -qm "initial"
PATH=.venv/bin:$PATH .venv/bin/python -m scripts.demo
```

`scripts/demo.py` walks: analyze → find duplicates → find dead code → generate docs → the full
campaign (audit → plan → confirm → verified execution) with a **planted behavior-breaking edit**
(tax 8% → 5%) that passes `pyright` but is **caught by `pytest`, rolled back, and re-proposed** →
dashboard before/after. This catch-and-rollback moment is the demo's centerpiece.

---

## `main` branch (the v3 engine)

On `main`, the **engine CLI is the primary front door**.

### Console entrypoints

Same two names (`refactorika`, `refactorika-scan`), both → `refactorika.cli:main`. But here `main`
is an engine runner, not a subcommand dispatcher.

### CLI (`refactorika/cli.py`)

```
refactorika <dir> [options]
```

Default behavior is a **dry-run on a throwaway copy**: it prints the leaf-to-root plan, each verified
edit with its gate results, and a before/after metrics table (LOC, complexity, dead-code count) plus
baseline/finale suite status. Then re-run with `--apply` to commit in place.

| Option | Effect |
|---|---|
| `--apply` | write changes in place and `git commit` each verified edit (instead of dry-run on a copy) |
| `--llm` | use the LLM planner (god-function decomposition); needs an API key, degrades to deterministic plan otherwise |
| `--agents` | run the specialist agent campaign (audit → plan → specialist agents) instead of the pipeline |
| `--show-graph` | print the symbol graph (symbols, entry points, dead code, cycles) and exit |
| `--show-plan` | print the leaf-to-root worklist and exit |
| `--show-memory` | print all stored `RefactorDecision`s and exit |
| `--show-similar QUALNAME` | embed the codebase and print semantic neighbors of a symbol; exit (needs embeddings) |
| `--no-tests` | skip the test gates (faster; baseline/finale not run) |
| `--rename a.b.qual=new_name` | pre-plan a reference-correct rope rename before the main planner runs (repeatable) |

### MCP server (`refactorika/mcp_server.py`) — 13 tools

The advisory tools `analyze_file`, `find_duplicates`, `find_dead_code`, `generate_docs`,
`get_context_map`, `get_log` and the verified-mutation tools `apply_and_verify`,
`apply_and_verify_multi` are present on both branches. `main` **adds the v3 engine tools** and uses a
different `run_agents`:

| Tool | Signature | Returns |
|---|---|---|
| `build_graph` | `build_graph(path)` | the Jedi symbol graph: symbols, `leaf_to_root` order, entry points, dead symbols, cycles |
| `get_plan` | `get_plan(path)` | the deterministic planner's leaf-to-root worklist of `TransformSpec`s |
| `run_pipeline` | `run_pipeline(path, apply=False)` | run the full verified pipeline; baseline/finale tests, records, before/after metrics |
| `run_agents` | `run_agents(path)` | run the agent campaign on `path` (audit → specialists → verified apply) |

> Note the **differences from `working`'s MCP**: `main` has `build_graph` and `run_pipeline`;
> `working` has `find_related`, `audit_repo`, and `confirm_plan` instead, and its `run_agents` takes
> `max_workers` (it dispatches an already-confirmed plan) rather than a `path`.

---

## The `EditRecord` (the verified-mutation contract) — [both]

Every verified mutation returns (or logs) an `EditRecord` (`core/schema.py`):

```python
{
  "file": str,
  "refactor_kind": str,                 # e.g. "reorder_imports", "remove_dead_code", "decompose_function"
  "checks": {"parse": bool|None, "lint": bool|None, "typecheck": bool|None, "tests": bool|None},
  "retries": int,
  "status": "committed" | "rolled-back" | "skipped-needs-human",
  "failure_reason": str|None,
  "diff": str,
  "files": [str, ...]                    # all files touched (multi-file edits)
}
```

`null` in `checks` means the gate was **skipped** (tool missing or no covering test) — recorded
explicitly, never silently passed. `skipped-needs-human` is the terminal safe state when retries are
exhausted: the engine reverts to the last good state rather than ever force-committing.
