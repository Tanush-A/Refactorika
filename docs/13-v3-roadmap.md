> **⚠ HISTORICAL — not maintained.** Preserved as a record of how the project evolved. For current docs see [docs/README.md](README.md).

# v3 Roadmap — Repo-Wide Refactoring, Call-Site Verification & Observability

> ⚠️ **SUPERSEDED — original brainstorm, kept for context.** The active, codebase-reconciled plan is **`docs/v3_spec.md`** (ships §0 CallGraph reconcile + §1 audit/plan/confirm + the campaign dashboard). The two items below that are *not* in the active build — the call-site sweep gate (§2) and Sentry (§3) — are parked in **`docs/v3-worklog.md`** with the reasons. Note this roadmap predates V2 and is stale where it treats `analysis/call_graph.py` as new (V2 already shipped it) — see the spec for the reconciliation.

> Builds on the shipped v1/v2 slice (`analyze_file` · `apply_and_verify` · `get_log`). v3 adds three things, in dependency order: a shared **call-graph index**, a **repo-wide audit + dependency-aware plan** with a human-confirm gate (#1), a **call-site sweep verification gate** (#2), and **Sentry observability** (cross-cutting). Inspiration is drawn from the `main` branch's convention-audit lineage, adapted to this branch's structural-refactoring thesis.

## Guiding invariant (unchanged)

The trust spine stays the same: **a mutation changes shape, not behavior, and nothing lands unverified.** v3 widens the blast radius Refactorika can reason about (single file → whole repo) and adds a gate shaped to the failure mode that widening creates (stranded cross-file references). Every external dependency degrades gracefully, matching the existing Redis→JSON fallback in `refactorika/core/storage.py`.

---

## 0. Shared foundation — call-graph / symbol index

Both #1 and #2 need the same primitive: *"which files/symbols reference symbol X?"* Build it once.

- **New module:** `refactorika/analysis/call_graph.py`.
- **Engine:** `tree-sitter-python` over the repo — collect `import` / `from … import` statements and `call` nodes, mapping `symbol → defining file` and `symbol → referencing sites (file:line)`. Grep fallback for references tree-sitter can't resolve statically.
- **Honest scope (stated, not hidden):** best-effort, **not** IDE-grade. Known false-negative sources — dynamic dispatch, `getattr` / string-keyed access, `__init__.py` re-exports, monkeypatching — are out of scope and documented as such. This mirrors the call-site honesty framing from the `main` lineage.
- **Caching:** reuse `Storage` AST-signature cache (`cache_get` / `cache_set`) so the graph isn't rebuilt every call.

**API (core, interface-agnostic):**
```python
def build_call_graph(repo_path: str, storage: Storage | None = None) -> CallGraph: ...
def dependents_of(graph: CallGraph, file_or_symbol: str) -> list[CallSite]: ...
```

---

## 1. Repo-wide audit + dependency-aware plan + human-confirm

Turns a pile of independent single-file edits into a guided, safe-by-ordering campaign.

### New advisory tools (read-only — no gate stack)

| Tool | Description |
|---|---|
| `audit_repo(path)` | Walk the repo, run the existing per-file analysis, aggregate + rank opportunities into one report. |
| `get_plan()` | Order deviating files **fewest-dependents-first** (via the call graph) so low-blast-radius edits land first. Persist a `Plan`. |
| `confirm_plan(decision)` | The single human checkpoint: approve or override the proposed plan before any mutation. Records the decision; no code changes. |

### Schema deltas (`refactorika/core/schema.py`)

```python
@dataclass
class PlanTask:
    file: str
    opportunities: list[Opportunity]
    dependents: list[str]   # call sites that depend on this file
    order: int              # execution index (fewest-dependents-first)

@dataclass
class Plan:
    repo: str
    dominant_finding: str | None   # the ranked headline opportunity
    tasks: list[PlanTask]
    confirmed: bool = False
```

### Storage deltas (`refactorika/core/storage.py`)

Add `append_plan(plan)` / `get_plan()` alongside the existing log/cache, with the same Redis→JSON fallback.

### Why it matters

- **Ordering reduces cascade risk** — editing heavily-depended-on modules last keeps later edits on stable ground.
- **Audit makes the workflow legible** — a ranked repo report is what a human can actually act on; a per-file analyzer can't show the forest.
- **One confirm step is the cheapest risk reduction** — the agent's *what/in-what-order* judgment is the most error-prone moment; confirming it once catches a bad plan before it propagates into a dozen mutations.

---

## 2. Call-site sweep as a new verification gate

Closes a real hole: today a moved/renamed symbol can leave a dangling reference in an **untested** file and still pass every gate, because `test_gate` only protects covered code (it returns `None`/skip when no tests collect — `refactorika/core/gates.py`).

### New advisory tool

| Tool | Description |
|---|---|
| `get_impact(path)` | Return known call sites / dependents for a file or symbol (also powers #1's ordering). |

### New gate

- **`callsite_sweep_gate(...)`** in `refactorika/core/gates.py`, returning the same `(Optional[bool], detail)` contract as the existing gates — `True` (no stranded refs), `False` (stranded refs → roll back), `None` (skipped/recorded, never silent).
- **Placement in `apply_and_verify`** (`refactorika/core/apply.py`): inserted **after `test_gate`**, preserving cheapest-first ordering (it is the most expensive, repo-wide check):

```
parse → lint → typecheck → pytest → callsite_sweep
```

- **What it checks:** after a green edit, re-scan the recorded call sites to confirm none were left referencing a now-moved/renamed/removed symbol. Surfaces stranded sites in `failure_reason` for the re-propose loop.

### Schema delta

Add `callsite_sweep: Optional[bool] = None` to `GateChecks` (`refactorika/core/schema.py`). The dashboard renders "N call sites checked, 0 stranded."

### Honest boundary

Catches incompletely-converted **known** sites; it cannot find sites the call graph never recorded (true false negatives). Framed as best-effort, consistent with §0.

---

## 3. Sentry observability (cross-cutting, env-gated)

### Research summary — how it actually integrates

- **Dedicated MCP integration.** The Sentry Python SDK ships a Model Context Protocol integration that **auto-instruments `FastMCP`** (the high-level API this server already uses in `refactorika/mcp_server.py`). It wraps tool / resource / prompt handlers, so each tool call becomes its own span with the tool name and arguments captured — **no per-tool manual wrapping required**.
- **Install:** `pip install "sentry-sdk"` (MCP support is part of the SDK and auto-enables when the `mcp` package is present). Add as an **optional** dependency in `pyproject.toml`.
- **Init** — once, alongside server startup in `refactorika/mcp_server.py`:

```python
import os
import sentry_sdk

_dsn = os.environ.get("SENTRY_DSN")
if _dsn:                                    # no DSN -> no-op, demo runs offline
    sentry_sdk.init(
        dsn=_dsn,
        traces_sample_rate=1.0,             # full tracing for the demo
        send_default_pii=False,             # do NOT ship source/code by default
        environment=os.environ.get("SENTRY_ENV", "dev"),
    )
```

- **Unhandled exceptions are auto-captured.** For the failures Refactorika currently swallows, capture explicitly so they surface with a stack trace instead of a one-line string:
  - the broad gate-crash handler (`refactorika/core/apply.py` — `except Exception`) → `sentry_sdk.capture_exception(exc)` before rolling back.
  - the silent Redis fallback (`refactorika/core/storage.py` — `except Exception: return None`) → add a breadcrumb/warning so degraded mode is visible.
- **Custom spans** for finer detail inside `apply_and_verify` — wrap each gate so latency is attributable:

```python
with sentry_sdk.start_span(op="gate", name="typecheck"):
    ok, detail = typecheck_gate(p)
```

### Environment

Add to `.env.example`: `SENTRY_DSN` (optional), `SENTRY_ENV` (optional). Absent DSN → SDK is a no-op; nothing else changes. Descopable to "logs only" without touching the pitch.

### What it does for *this* project

- **Makes swallowed failures visible** — gate crashes, subprocess errors (`ruff`/`pyright`/`pytest` shelled out with no timeout), and silent Redis fallback become captured events with tracebacks.
- **Per-tool + per-gate latency** — see which gate dominates (typically `pytest`/`pyright`) and the added cost of the new `callsite_sweep`.
- **One connected trace** of a full run: `audit_repo → get_plan → apply_and_verify × N`, including rollbacks and re-proposes — the demo trace asset.
- **Error grouping** across runs — repeated audit/parse failures collapse into one counted issue.

### Honest caveats (carry into the demo, don't oversell)

- **Sentry measures the harness, not the refactor.** It has no ground truth: it cannot report call-site false-negative rate or whether an edit was *correct*. A green Sentry dashboard means "the machinery didn't crash," not "the edits were safe." (Correctness numbers would come from a ground-truth eval — explicitly deferred, not in v3.)
- **Token/cost tracking largely does not apply here.** Sentry's AI monitoring captures tokens when *Refactorika* calls a model. In the primary MCP flow the **agent proposes edits and Refactorika only verifies** — so Refactorika may make zero LLM calls and there are no tokens to capture. This benefit only materializes in a CLI flow where Refactorika itself proposes edits. Do not bank on it.

---

## Resulting surface

```
Advisory:   analyze_file · audit_repo · get_plan · confirm_plan · get_impact · get_log
Mutation:   apply_and_verify   (gates: parse → lint → typecheck → pytest → callsite_sweep)
Cross-cut:  Sentry FastMCP auto-instrumentation + explicit capture of swallowed failures (DSN-gated)
```

## Build order

1. **`analysis/call_graph.py`** — shared dependency; unblocks #1 and #2.
2. **`get_impact` + `callsite_sweep_gate`** (#2) — smallest surface, hardens the existing spine first.
3. **`audit_repo` / `get_plan` / `confirm_plan`** (#1) — the campaign layer on top.
4. **Sentry** — last, DSN-gated, descopable to logs-only.

## Open items to confirm before coding

- Freeze the new tool signatures + `PlanTask`/`Plan` schema and the `callsite_sweep` field on `GateChecks` (the frozen interface is the contract).
- Confirm `callsite_sweep` runs after `pytest` (cost ordering) vs. as a pre-edit advisory check — current recommendation: post-`pytest` gate.
- Confirm `sentry-sdk` version pin and that the MCP integration auto-enables under the installed `mcp` SDK version.
