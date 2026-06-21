# Risks & Future Scope

## Key risks

- **Generalization risk** — convention detection working reliably only on the curated demo repo, not arbitrary code. Mitigated by being explicit in the pitch about current scope (one language, one pattern type).
- **Call-site accuracy risk** — grep/LLM-based dependency tracking will have false negatives compared to a real IDE. Acceptable for demo if framed honestly.
- **Time risk** — audit step is the most open-ended; should be timeboxed hardest and descoped first if behind schedule.
- **Harness dependency risk** — the typecheck gate ([05a-verification-harness.md](05a-verification-harness.md)) depends on the demo repo having a working `tsconfig.json` and a fast `tsc --noEmit`; large projects may make this slow. Mitigated by single-file-scope checking and keeping the demo repo small. Timebox the `tsc` integration.

## Future scope (explicitly out of v1)

- Multiple convention types audited simultaneously.
- Persistent memory across sessions/repo lifecycle (the Reach upgrade of the Redis long-term tier — see [06-redis-integration.md](06-redis-integration.md)).
- Vector-search-based rule retrieval — valuable once many convention types exist; unnecessary for v1's single type.
- Incorporating human review corrections as a second rule source.
- Multi-language support.

## Build plan / time estimate (hackathon)

| Component | Estimate |
|---|---|
| Convention audit (TypeScript, error-handling) + human-confirm step | 4-6 hrs |
| Refactor plan / call-site detection (AST + grep) | 3-5 hrs |
| Guided execution + consistency checks | 2-3 hrs |
| Verification harness (parse gate, `tsc` gate, sweep, re-propose loop) | 2-3 hrs |
| Context efficiency layer + comparison metric | 2-3 hrs |
| Context file generation | 1-2 hrs |
| Redis integration — storage, Agent Memory, Context Retriever, LangCache **[Initial]** | 3-5 hrs |
| Demo repo construction + dashboard | 3-5 hrs |
| **Total (Initial)** | **22-32 hrs** |
| Sentry integration — SDK + per-tool spans **[Reach]** | +1-2 hrs |

**Build order:** ship a vertical slice (one file, end-to-end: audit → confirm → plan → check → verify → commit) on a 2-file repo *before* broadening to 10-15 files. This guarantees a demoable artifact even if audit generalization lags.
