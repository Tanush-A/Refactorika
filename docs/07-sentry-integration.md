# Sentry Integration — **[Reach]**

Describes how Sentry AI Agent Monitoring slots into the existing architecture ([04-architecture.md](04-architecture.md)) **without changing project scope**. Read alongside [06-redis-integration.md](06-redis-integration.md).

## Why it fits

The Sentry track rewards strong technical execution paired with observability/error monitoring, not just a working demo. Edit Memory runs an agent loop over many MCP tool calls, and Sentry surfaces where those calls *throw, fail, or slow down* live — turning tool-level reliability into a visible signal. (The call-site *false-negative* rate is measured separately by the ground-truth eval in [09-success-metrics-and-demo.md](09-success-metrics-and-demo.md), not by Sentry, which has no ground truth; the two are complementary.)

Sentry also directly supports instrumenting MCP servers (tool executions, prompt retrievals, resource access), which matches Edit Memory's delivery form without needing custom monitoring code.

## Component mapping

- **MCP tool instrumentation → reliability of the core mechanism**
  - Instrument `check_convention`, `get_impact`, and `record_edit` individually.
  - Track per-tool *error/exception* rate and latency — surfaces tools that throw or fail. (Sentry **cannot** measure false negatives / silently-missed call sites, since it has no ground truth; that number comes from the ground-truth eval, not Sentry.)
  - This makes tool-level failures a measured, visible number instead of an assumption.
- **Trace view → demo asset**
  - A single end-to-end trace covers the audit → plan → guided execution pipeline: model calls, tool executions, and MCP interactions in one view.
  - Useful on screen during the live demo as a literal trace of what happened during a refactor run, alongside the audit report and token chart.
- **Token/cost tracking → second source for the efficiency metric**
  - Sentry's AI monitoring captures token usage and cost per model call automatically.
  - Gives a second, independently-sourced version of the token-usage comparison vs the realistic agent-loop baseline, without building that measurement by hand.
- **Error tagging/grouping → audit and execution failure patterns**
  - Automatic grouping of similar failures across runs — useful if the audit step misclassifies a pattern repeatedly in a particular kind of file; surfaces that as a single grouped issue rather than scattered noise.

## Architecture note

- Sentry SDK initialized alongside the MCP server, with tracing enabled (`tracesSampleRate`) and the relevant AI/agent integration for whichever model client is used.
- MCP tool calls (`check_convention`, `get_impact`, `record_edit`) get wrapped so each shows up as its own span — gives per-tool failure rates, not just an aggregate.
- Setup is lightweight (SDK init + integration registration) relative to the Redis provisioning work — can be added late without much schedule risk.

## Demo addition

Alongside the core demo script and the Redis Insight addition ([09-success-metrics-and-demo.md](09-success-metrics-and-demo.md)):

- Show a Sentry trace of one full refactor run: audit call, plan generation, each guided edit, and the consistency checks, as a single connected trace.
- Show the per-tool dashboard: `check_convention` and `get_impact` *error/exception* rates over the demo run, paired with the ground-truth precision/recall numbers (the actual source for false-negative rate) — together substantiating the project's honesty about call-site detection being best-effort rather than IDE-grade.

## Risk

- Minimal added risk — this is the lightest of the three integrations (PRD core, Redis, Sentry) to bolt on, and can be the first thing descoped back to "logs only" if time runs short without losing the core pitch.
