# Risks & Future Scope

## Key risks

- **Generalization risk** — convention detection working reliably only on the curated demo repo, not arbitrary code. Mitigated by being explicit in the pitch about current scope (one language, one pattern type).
- **Call-site accuracy risk** — AST/grep-based dependency tracking will have false negatives compared to a real IDE (dynamic dispatch, `getattr`/string-keyed access, `__init__.py` re-exports, monkeypatching). Acceptable for the demo if framed honestly; the real number comes from the ground-truth eval (see [09-success-metrics-and-demo.md](09-success-metrics-and-demo.md)), not from a claim of completeness.
- **Inferred / unannotated-type blind spot** — detection is tree-sitter-only (see [05-core-components.md](05-core-components.md) §5.1), so it sees *syntax*, not resolved types: functions with unannotated returns, or `Result` aliases defined in another file, are missed or left unclassified. Accepted for v1 and framed honestly; a full type-resolver (`pyright` as a detection engine, not just a gate) would close this gap (future scope). The curated demo repo should use explicit return annotations so the audit reflects true adoption.
- **Time risk** — the audit step is the most open-ended; it should be timeboxed hardest and descoped first if behind schedule.
- **Harness dependency risk** — the typecheck gate ([05a-verification-harness.md](05a-verification-harness.md)) depends on the demo repo having a working `pyright` setup and a fast run; large projects may make it slow. Mitigated by single-file-scope checking and keeping the demo repo small. Timebox the `pyright` integration.
- **Test-gate dependency risk** — the behavioral test gate (§5.5) depends on the demo repo shipping a fast, deterministic `pytest` suite that covers the touched files; flaky or slow tests would stall the loop or cause false rollbacks. Mitigated by curating a small deterministic suite scoped to the refactored modules, and by recording a skip (not a silent pass) where no test covers a file. Descopable to logs-only if behind schedule.

## Future scope (explicitly out of v1)

- Multiple convention types audited simultaneously.
- Persistent memory across sessions/repo lifecycle (the Reach upgrade of the Redis long-term tier — see [06-redis-integration.md](06-redis-integration.md)).
- Vector-search-based rule retrieval — valuable once many convention types exist; unnecessary for v1's single type.
- Incorporating human review corrections as a second rule source.
- Multi-language support.

## Build plan / time estimate (hackathon)

| Component | Estimate |
|---|---|
| Convention audit (Python, error-handling) + human-confirm step | 4-6 hrs |
| Refactor plan / call-site detection (AST + grep) | 3-5 hrs |
| Guided execution + consistency checks | 2-3 hrs |
| Verification harness (parse gate, `pyright` gate, call-site/handled-result sweep, re-propose + escalation) | 2-3 hrs |
| Lint/format (`ruff`) + behavioral test (`pytest`) gates | 1-2 hrs |
| Context efficiency layer + comparison metric | 2-3 hrs |
| Context file generation | 1-2 hrs |
| Redis integration — Agent Memory, Context Retriever, LangCache **[Initial]** | 3-5 hrs |
| Demo repo construction + dashboard | 3-5 hrs |
| **Total (Initial)** | **21-34 hrs** |
| Sentry integration — SDK + per-tool spans **[Reach]** | +1-2 hrs |

**Build order:** ship a vertical slice (one file, end-to-end: audit → confirm → plan → check → verify → commit) on a 2-file repo *before* broadening to 10-15 files. This guarantees a demoable artifact even if audit generalization lags. Within the harness, land gates in order of value-per-hour: parse + `pyright` first, then the behavioral `pytest` gate, then `ruff` lint/format.
