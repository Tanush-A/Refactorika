# Success Metrics & Demo Script

## Success metrics

- Audit correctly identifies the dominant convention and flags deviating files on a constructed/curated demo repo with known, deliberate inconsistency.
- Guided execution catches at least one deliberately planted convention violation and one *planted, ground-truth-known* missed call site, live, in the demo.
- **Ground-truth eval:** on the curated demo repo (whose true call-site set is known), report call-site detection precision/recall. This is the honest source for any false-negative number — **not** Sentry (see [07-sentry-integration.md](07-sentry-integration.md)).
- **Every committed edit passes the parse + `pyright` gate**; no edit is committed in a type-error state.
- **The reject → re-propose loop demonstrably recovers** from a deliberately planted bad edit (rollback + successful re-proposal), live, in the demo.
- **Context files** are generated for the refactored modules and accurately reflect the post-refactor convention and key dependents (see [05-core-components.md](05-core-components.md) §5.6).
- Token usage for audit + refactor is a fraction of the realistic agent-loop baseline on the demo repo. Scaling claims (sub-linear in repo size) require multiple repo sizes to demonstrate and are a **[Reach]** measurement.

## Demo script

1. Show the demo repo: deliberately inconsistent error handling across ~10-15 files.
2. Run audit → show report (dominant pattern, deviating files).
3. Run plan → show ordered task list with call-site counts.
4. Run guided execution → watch 3-4 files get fixed; live catch of a violation and a *planted, ground-truth-known* missed call site.
5. **Plant a bad edit** → show the pre-edit/typecheck gate reject it, roll back, and the agent recover via the re-propose loop.
6. Show token-usage chart: Edit Memory vs the realistic agent-loop baseline.
7. Open a generated context file for a refactored module — show it accurately reflects the new convention and its dependents.

Plus integration-specific additions:
- Redis Insight view of memory entries building up live — see [06-redis-integration.md](06-redis-integration.md).
- Sentry trace of one full refactor run + per-tool error-rate dashboard — see [07-sentry-integration.md](07-sentry-integration.md).
