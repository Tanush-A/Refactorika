# Tech Stack

> Reflects the as-built v3 engine. See [v3_spec.md](v3_spec.md) for how the pieces fit.

## Language
- **Python 3.11+** — the engine *and* the target it refactors.

## Program model (correctness foundation)
- **`jedi`** — real name binding / reference resolution. Builds the symbol graph in
  `graph/resolver.py`: resolves each use to its true definition across imports, aliases, scopes,
  and method dispatch. This is what replaces the old regex call-graph and makes renames and
  dead-code analysis reference-correct.
- **`tree-sitter` + `tree-sitter-python`** — fast structural parsing (function spans, canonical
  type-stream fingerprints for the decision-memory shape key, the parse gate).

## Transform engines (the only code that mutates source)
- **`rope`** — turnkey, semantic cross-file **rename-propagation**; we extract the new file
  contents from its changeset *without applying to disk* so the checker controls commit.
- **`libcst`** — lossless CST for **node replacement** (god-function decomposition) and surgical
  **dead-code removal**; preserves formatting and comments.
- **`ruff` + `autoflake`** — deterministic **cleanup** (unused imports/vars, simplifications,
  modern syntax, formatting); zero LLM.

## Verification gate stack (cheapest-first, short-circuit)
- **`tree-sitter`** — parse gate (no `ERROR`/`MISSING` nodes), before touching disk.
- **`ruff`** — lint gate; only *new* violations vs. a pre-edit baseline are rejected.
- **`pyright`** — type gate; zero errors.
- **`pytest`** — behavior gate, **impact-scoped** (only tests reachable from the changed symbol).
  Full suite at baseline + finale. Type-clean ≠ behavior-preserving — this is the real proof.
- **`git`** — atomic apply/revert; each verified edit is its own commit, rollback restores files.

## Judgment layer
- **`anthropic`** (Claude, temp 0) — used only for judgment (which god function to decompose, how
  to name helpers), returns structured specs. Wrapped with a **record/replay cache** + stub seam
  so runs are reproducible and tests/demos are fully offline; degrades to the deterministic plan
  with no API key.

## Front doors
- **`typer`** — the standalone CLI (`refactorika <dir>` + `--apply`/`--show-graph`/`--show-plan`/`--llm`).
- **`mcp` (`FastMCP`)** — the MCP server (`build_graph`, `get_plan`, `run_pipeline`, `get_log`).

## Metrics
- **`radon`** — LOC + cyclomatic complexity for the before/after report (with the graph's
  dead-code count).

## Memory & state — Redis Iris (primary, mandatory local-JSON fallback)
- **Graph + leaf-to-root order** — queryable program state.
- **Decision memory** — `RefactorDecision` keyed by structural shape, so the same situation is
  refactored the same way across the repo (the "beyond a cache" differentiator).
- **Vector index** — per-function embeddings for duplicate detection / similar-refactor exemplars
  (optional `[semantic]` extra: `sentence-transformers` local, or OpenAI).
- **Fallback:** every component degrades to `.refactorika/` files; kill Redis and results are
  identical. Redis is an optimization (and the live demo's Redis Insight view), never required.

## Testing
- **`pytest`** — offline suite (stubbed LLM/embedder, no Redis): resolver correctness, each
  engine, the verified spine, and the LLM + decision-memory loop.
