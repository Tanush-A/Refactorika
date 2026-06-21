# v3 Worklog — parked items (not in the active `v3_spec.md`)

> Things from the v3 roadmap (`docs/13-v3-roadmap.md`) deliberately deferred out of the active v3 build. The active spec (`docs/v3_spec.md`) ships §0 CallGraph reconcile + §1 audit/plan/confirm + the campaign dashboard. These two are parked — both real, both need more thought before they help a demo.

## P1 — Call-site sweep verification gate (roadmap §2) ⏸️

**What it was:** a new gate `callsite_sweep_gate` (+ a `get_impact` advisory tool + a `callsite_sweep` field on `GateChecks`) inserted into `apply_and_verify` after `pytest`. After a green edit, re-scan the recorded call sites to confirm a moved/renamed/removed symbol didn't leave a dangling reference in an **untested** file (which the `pytest` gate can't catch because it skips uncovered code).

**Why it's compelling:** it closes a real hole and fits the "visible verification" thesis — *"we catch the thing your tests miss."*

**Why it's parked:**
- It rides entirely on `CallGraph`'s name resolver, which the v2 review flagged as **best-effort** — dynamic dispatch, `getattr`/string keys, `__init__.py` re-exports, aliased imports, and same-named symbols across modules all produce false or missing edges. A *gate* built on a shaky resolver is **demo-flaky**: false "stranded reference!" alarms that roll back good edits, or missed strandings that undermine the claim.
- The honest version needs the resolver hardened first (real binding/scope resolution, not suffix-matching), which is a bigger task than the gate itself.

**To unpark, do one of:**
- **(a) Harden the resolver** (replace suffix-matching with real scope resolution), then add the gate as designed — the principled path.
- **(b) Curated-only scripted beat** — demo it on a single planted case (a renamed symbol with one dangling ref in an untested file) where it reliably fires, and frame it honestly as best-effort. Cheap demo value without trusting it broadly.

**Build sketch (when unparked):** `get_impact(path) -> {symbol, call_sites: [{file, line}]}`; `callsite_sweep_gate(repo, touched_symbols) -> (Optional[bool], detail)` returning the standard gate contract (`True` no strandings / `False` strandings → roll back / `None` skipped-and-recorded); insert after `test_gate` in `apply_and_verify`; add `callsite_sweep: Optional[bool]` to `GateChecks` and render it in the dashboard ("N call sites checked, 0 stranded").

## P2 — Sentry observability (roadmap §3) ⏸️

**What it was:** env-gated (`SENTRY_DSN`) Sentry SDK init in `mcp_server.py`; the Sentry MCP integration auto-instruments `FastMCP` so each tool call becomes a span; explicit `capture_exception` on the swallowed gate-crash and the silent Redis fallback; per-gate spans for latency. Absent DSN → no-op (offline demo unaffected).

**Why it's parked — refine the usage first:**
- **It measures the harness, not the refactor.** A green Sentry dashboard means "the machinery didn't crash," not "the edits were correct/safe." We need to decide what story it actually tells in the demo before wiring it, so it doesn't over-promise.
- **Token/cost tracking largely doesn't apply.** Sentry's AI monitoring captures tokens when *Refactorika* calls a model — but in the primary MCP flow the agent proposes and Refactorika only verifies, so Refactorika may make zero LLM calls. That benefit only exists in a CLI flow where Refactorika itself proposes.
- So before building: decide the **concrete demo narrative** (e.g. "one connected trace of `audit → plan → apply ×N` with per-gate latency, showing which gate dominates and that force-commits stayed 0") and whether a Sentry sponsor/ops angle justifies it.

**Why it's cheap when we want it:** the integration auto-instruments FastMCP — no per-tool wrapping. Add `sentry-sdk` as an optional dep, init once if `SENTRY_DSN` is set, add a couple of `capture_exception` calls. ~1 hour, fully additive, descopable to logs-only.

**To unpark:** lock the demo narrative above, then implement the env-gated init + explicit captures + per-gate spans. Add `SENTRY_DSN`/`SENTRY_ENV` to `.env.example` (optional).
</content>
