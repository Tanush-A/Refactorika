# Redis Iris Integration — **[Initial]**

Describes how Redis Iris slots into the existing architecture ([04-architecture.md](04-architecture.md)) **without changing project scope**.

## Why it fits

The Redis track judging criteria specifically calls out using Iris for agent memory, vector search, and context retrieval — not just caching. Edit Memory's core mechanism (a rule list that needs to be retrieved selectively, plus structured lookups like call-site tracking) maps directly onto Iris's actual components rather than needing a bolted-on justification.

## Component mapping

- **Redis Agent Memory → the rule list**
  - Long-term memory tier stores inferred conventions as they're extracted during the audit and refactor. For **Initial**, this persists *within the current run*; **cross-session reuse across repo lifecycles is [Reach]** (see [02-target-user.md](02-target-user.md)).
  - Replaces a flat JSON rule file with something queryable. Note: v1 has a single convention type, so selective retrieval has limited payoff initially — its value (pulling only the rules relevant to a file) scales with convention count.
  - Session memory tier holds the in-progress refactor task list and execution log for the current run — gives you the ordered event log for free instead of building your own.
- **Redis Context Retriever → `check_convention` / `get_impact`**
  - Context Retriever's model is typed, chainable tool calls over structured data rather than one-shot vector retrieval — exactly the shape these two MCP tools already need.
  - Define structured lookups (e.g. "all call sites for function X," "current dominant convention for pattern Y") as Context Retriever tools. The agent calls them mid-refactor the same way it would call any other MCP tool, and the retrieval logic doesn't have to be hand-rolled.
- **Redis LangCache → audit efficiency**
  - The audit step makes repeated classification calls across files ("does this file use exceptions or `Result<T>`?"). LangCache caches these — keyed on the *normalized AST signature* of the construct, **not** loose semantic similarity, to avoid false cache hits that would corrupt audit accuracy.
  - This becomes a clean, legitimate "Redis beyond caching" story: caching is one piece, not the whole pitch — agent memory and context retrieval do the structural work.
- **Vector search (underlying both Agent Memory and Context Retriever) — [Reach]**
  - v1's three fixed, AST-detectable variants are matched *exactly* (more accurate than fuzzy matching here). Semantic vector matching becomes useful only once many convention types exist; it is a Reach capability, not an Initial dependency.

## Architecture note (relative to §6 / [04-architecture.md](04-architecture.md))

- Local JSON storage becomes the fallback/offline mode.
- Primary mode for the demo: Redis Cloud instance backing Agent Memory (rules + session log) and Context Retriever (call-site/dependency lookups).
- MCP server tools (`run_audit`, `confirm_convention`, `get_plan`, `check_convention`, `get_impact`, `verify_edit`, `run_typecheck`, `record_edit`) call into Redis under the hood instead of reading/writing local JSON.

## Demo addition

Alongside the core demo script ([09-success-metrics-and-demo.md](09-success-metrics-and-demo.md)):

- A short Redis Insight view showing the long-term memory entries building up live as conventions are extracted — makes the "memory" claim visible, not just asserted.
- A note on the token-usage chart distinguishing LLM-call savings from LangCache vs the structural savings from not reloading full files.

## Risk

- Added infra dependency (Redis Cloud setup, account/connection) on top of the existing build risks. Budget setup time early — don't leave Redis provisioning to the last few hours.
