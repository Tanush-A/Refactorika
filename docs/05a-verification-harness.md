# Verification Harness (§5.5)

Automated guardrails layered on top of guided execution ([05-core-components.md](05-core-components.md) §5.3). Every proposed edit passes through this pipeline before it is committed:

1. **Pre-edit gate** — the proposed edit is parsed with `tree-sitter-typescript`; reject if it fails to parse or does not match the confirmed target variant.
2. **Post-edit type check** — run `tsc --noEmit` (project scope, or single-file scope where configured) on touched files; if it fails, roll the edit back.
3. **Call-site sweep** — after a successful edit, re-scan the *recorded* call sites (AST + grep) to confirm none were left in the old convention; surface any stragglers. Note: this catches incompletely-converted *known* sites; it cannot find sites the §5.2 detection never recorded (true false negatives), which are addressed only by the ground-truth eval (see [09-success-metrics-and-demo.md](09-success-metrics-and-demo.md)).
4. **Reject → re-propose loop** — on any gate failure, surface the failure reason to the agent and let it re-propose, up to a bounded retry count.
5. **Per-edit audit log** — append a structured record (file, checks run, pass/fail, retry count, final diff) to the local JSON store (or Redis — see [06-redis-integration.md](06-redis-integration.md)), powering the demo dashboard.

This is the mechanism that makes the "agent did this work safely" claim credible — see [01-problem-and-purpose.md](01-problem-and-purpose.md): trust comes from verification gates, not from prompting the agent to be careful.
