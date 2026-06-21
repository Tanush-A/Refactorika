> **⚠ HISTORICAL — not maintained.** Preserved as a record of how the project evolved. For current docs see [docs/README.md](README.md).

# Redis Iris — The Shared Brain

> **See [v3_spec.md §8](v3_spec.md) for the as-built design.** This page summarizes the role
> Redis plays in the v3 engine.

Refactorika uses Redis Iris as live decision state, not a dumb cache — with a **mandatory
local-JSON fallback** so everything runs offline (kill Redis and results are identical).

- **Graph + leaf-to-root order** — the reference-correct symbol graph and its apply order are
  queryable program state the pipeline plans on.
- **Decision memory** *(the differentiator)* — every judgment the LLM makes is recorded as a
  `RefactorDecision` keyed by the code's **structural shape**. Before decomposing a function, the
  planner **recalls** how an identical shape was handled before and **reuses the same helper
  names** — so the 2nd/5th/Nth similar function stays consistent. The engine remembers its own
  conventions instead of re-deciding per file. This is "Redis beyond caching": live memory that
  changes the *output*, not just the speed. (`memory/agent_memory.py`)
- **Vector index** — per-function embeddings for duplicate detection and similar-refactor
  exemplars (optional `[semantic]` extra; local `sentence-transformers` or OpenAI).

The demo beat: open Redis Insight and watch the decision entries build up live, then see two
identical-shape functions get the *same* helper names because the second refactor recalled the
first.
