# Refactorika v3 — Build Spec

> v3 widens the harness from **one file** to a **whole-repo campaign with a human in the loop**: audit the repo → propose a dependency-ordered plan → a human confirms → execute the existing verified single-file edits in that order → report before/after. It is **purely additive** — the verified mutation spine (`apply_and_verify` / `apply_and_verify_multi` + the gate stack) is untouched.
>
> Supersedes `docs/13-v3-roadmap.md` (which predates V2 and is now partly stale). Two roadmap items are **parked** to `docs/v3-worklog.md`: the call-site sweep gate (needs the call-graph resolver hardened first) and Sentry (needs its usage refined). Built for a **hackathon** — demo-able and visible over exhaustive.
>
> **Scope this delivers:** §0 CallGraph reconcile · §1 `audit_repo` / `get_plan` / `confirm_plan` · the campaign dashboard + before/after health number.

---

# PART A — Change spec (deltas to the current codebase)

Everything v3 touches, file by file, stated against what's actually in the tree today. `(new)` = add, `(extend)` = modify, `(unchanged)` = do not touch.

## A.0 Reconcile with V2 — `refactorika/analysis/call_graph.py` (extend)
The roadmap treats `call_graph.py` as a new module; **it already exists** (V2). It has `CallGraph.build(path)` (classmethod), `call_sites(name)`, `edges_from(qualname)`, `all_symbols()`. Dependents logic currently lives **inline** in `memory/context.py::_dependents_from_call_graph`.
- **Add** `CallGraph.dependents_of(module: str) -> list[str]` — promote the exact logic from `context._dependents_from_call_graph` (modules referencing `module` by final segment) onto the class.
- **Add** `CallGraph.dependent_count(module: str) -> int` (or just `len(dependents_of(...))`) for plan ordering.
- **Refactor** `memory/context.py` to call `cg.dependents_of(...)` instead of its private copy (kills the duplication; this is the only v2-review cleanup we fold in because v3 depends on it).

## A.1 New MCP tools — `refactorika/mcp_server.py` (extend)
Add three **advisory** tools (read-only; no gate stack). Freeze these signatures:
```python
@mcp.tool()
def audit_repo(path: str) -> dict: ...        # ranked repo-wide opportunity report
@mcp.tool()
def get_plan(path: str) -> dict: ...           # dependency-ordered plan; persists it
@mcp.tool()
def confirm_plan(decision: str = "approve", order: list[str] | None = None) -> dict: ...
```
The 8 existing tools stay as-is. Total surface becomes 11.

## A.2 Schema additions — `refactorika/core/schema.py` (extend)
Add dataclasses (same `to_dict()` style as the existing `Opportunity`/`EditRecord`; `Plan` also gets `from_dict()` for rehydration from storage):
```python
@dataclass
class AuditEntry:           # one file in the repo audit
    file: str
    opportunities: list[Opportunity]
    score: int             # sum of opportunity ranks — the file's "messiness"

@dataclass
class RepoAudit:
    repo: str
    files_scanned: int
    total_opportunities: int
    by_kind: dict           # {refactor_kind: count}
    dominant_finding: str | None   # headline: highest-aggregate-rank kind
    entries: list[AuditEntry]      # sorted by score desc

@dataclass
class PlanTask:
    file: str
    opportunities: list[Opportunity]
    dependents: list[str]   # modules that depend on this file (call graph)
    order: int              # execution index, fewest-dependents-first

@dataclass
class Plan:
    repo: str
    dominant_finding: str | None
    tasks: list[PlanTask]
    confirmed: bool = False
    decision: str | None = None    # "approve" | "reject" | "reorder"
```
Reuse the existing `Opportunity(kind, location, detail, rank)` verbatim — do not introduce a parallel type.

## A.3 Storage additions — `refactorika/core/storage.py` (extend)
Persist the *current* plan (single value, overwritten — not a list) so `confirm_plan` can mutate it. Same Redis-primary / JSON-fallback pattern as `append_log`/`cache_*`:
```python
def save_plan(self, plan: dict) -> None: ...   # Redis key "refactorika:plan" / JSON "plan"
def load_plan(self) -> dict | None: ...
```
- Named `save_plan`/`load_plan` (not `get_plan`) to avoid confusion with the `get_plan` **tool**.
- `_read_json` default currently `{"log": [], "cache": {}}`; read `plan` with `.get("plan")` (don't require it in the default), consistent with how `vectors`/`context` are handled.

## A.4 Repo audit + planning core — `refactorika/analysis/audit.py` (new)
Interface-agnostic core the tools wrap:
```python
def audit_repo(path: str, storage: Storage) -> RepoAudit: ...
def build_plan(path: str, storage: Storage) -> Plan: ...
```
- Reuse the **existing** `_collect_py_files` from `call_graph.py` (NOT the divergent copy in `duplicates.py`) so audit, dead-code, and planning scan the same file set.
- Reuse the **existing** `analyze.analyze_file(path, storage)` / `_analyze` per file — no new analysis logic. (Cache hits via Storage make repeat audits cheap.)
- `build_plan` builds one `CallGraph.build(path)` and orders deviating files by `dependent_count` ascending (low blast radius first), tie-break by audit `score` desc.

## A.5 Campaign dashboard — `refactorika/dashboard.py` (extend)
Today `render(log)` renders only the edit log. Add (keep the existing `render` working):
```python
def render_audit(audit: dict) -> str: ...      # ranked file table + by-kind totals + headline
def render_plan(plan: dict) -> str: ...         # ordered tasks: order · file · #opps · dependents · CONFIRMED?
def render_campaign(audit_before, plan, log, audit_after) -> str: ...  # the full visible story + before/after health
```
`render_campaign` is the demo money-shot: audit → plan (with the confirm state) → the gate log of executed edits → a one-line **before → after** health delta (total opportunities, avg nesting, files improved).

## A.6 Demo — `scripts/demo.py` (extend)
Add a campaign act after the existing single-file moment: `audit_repo(demo_repo) → get_plan → confirm_plan("approve") → apply_and_verify the planned edits in order → render_campaign` with the before/after health line. Must run offline (no `[semantic]` needed).

## A.7 Unchanged (do NOT touch)
`core/apply.py`, `core/gates.py`, the `EditRecord`/`GateChecks` schema, and all 8 existing tools. v3 adds a planning/confirmation layer *on top of* the verified spine; it does not modify verification.

## A.8 Parked → `docs/v3-worklog.md` (not in this spec)
- **Call-site sweep gate** (roadmap §2): a new `callsite_sweep_gate` + `get_impact` + a `GateChecks.callsite_sweep` field. Parked because it rides on the call-graph resolver, which the v2 review flagged as best-effort (false edges) — a gate on a shaky resolver is demo-flaky. Revisit after hardening resolution; or ship as a *curated-only* scripted beat.
- **Sentry observability** (roadmap §3): refine *how* it's used first (it measures the harness, not correctness; token tracking doesn't apply to the verify-only flow). Env-gated, additive, low-effort when we want it.

---

# PART B — Full spec

## 1. What v3 is

v1/v2 made a *single* structural edit safe and visible. v3 makes a *campaign* of them safe, ordered, and human-approved across a repo. The thesis is unchanged — **shape, not behavior; nothing lands unverified** — but v3 adds the two things a multi-file campaign needs that a single edit doesn't:

1. **Forest-level legibility** — a ranked repo report a human can actually act on (`audit_repo`), where a per-file analyzer only shows trees.
2. **Order + consent** — a dependency-aware plan (`get_plan`) and a single human checkpoint (`confirm_plan`) before any mutation. The agent's most error-prone judgment is *what to change and in what order*; confirming it once catches a bad plan before it propagates into a dozen edits.

Execution itself is the **existing** `apply_and_verify` loop, run task-by-task in plan order. v3 adds no new mutation path and no new gate.

## 2. The campaign flow (the golden path v3 adds)

```
audit_repo(repo)        ── ADVISORY ─▶ ranked report: which files, which smells, headline finding
        │
get_plan(repo)          ── ADVISORY ─▶ tasks ordered fewest-dependents-first; persisted as the current Plan
        │
confirm_plan("approve") ── HUMAN GATE ─▶ marks the Plan confirmed (or reject / reorder). No code changes.
        │
   for task in plan.tasks (in order):              ← Claude drives, one task at a time
        Claude proposes new content for task.file
        apply_and_verify(task.file, new_content, kind)   ← EXISTING verified spine: parse→ruff→pyright→pytest
              ├─ green → commit, EditRecord(committed)
              └─ fail  → rollback, EditRecord(rolled-back, reason) → Claude re-proposes
        │
render_campaign(...)    ─▶ audit · plan · gate log · before→after health
```

**Why the agent stops at `confirm_plan`:** the plan is persisted with `confirmed=False`. The intended contract (enforced by convention + the demo, not by the gate stack) is that Claude does not begin `apply_and_verify` on planned tasks until a human has called `confirm_plan`. This keeps the human-in-the-loop moment real and visible.

## 3. `audit_repo` — repo-wide opportunity report

**Algorithm:**
1. `files = _collect_py_files(path)` (the `call_graph.py` collector — skips `.venv/__pycache__/.git/.*_cache`).
2. For each file, `analyze_file(file, storage)` (cached on content signature → cheap re-runs).
3. Aggregate: `total_opportunities`, `by_kind` counts, per-file `AuditEntry` with `score = sum(opp.rank for opp in file.opportunities)`.
4. `dominant_finding` = the `kind` with the highest summed rank across the repo (the single thing most worth doing), rendered as `"flatten_nesting (14 sites, top: orders.compute_total)"`.
5. `entries` sorted by `score` desc (messiest files first).

**Return** (`RepoAudit.to_dict()`):
```json
{
  "repo": "demo_repo/",
  "files_scanned": 2,
  "total_opportunities": 5,
  "by_kind": {"flatten_nesting": 1, "reorder_imports": 2, "split_function": 2},
  "dominant_finding": "reorder_imports (2 sites)",
  "entries": [
    {"file": "demo_repo/orders.py", "score": 142,
     "opportunities": [{"kind": "flatten_nesting", "location": "compute_total (line 18)", "detail": "nesting depth 5 (> 3)", "rank": 100}, ...]}
  ]
}
```
Read-only; no mutation, no gate stack.

## 4. `get_plan` — dependency-ordered plan

**Algorithm:**
1. `audit = audit_repo(path)`; the deviating files are `audit.entries` with ≥1 opportunity.
2. `cg = CallGraph.build(path)` (one build for the whole repo).
3. For each deviating file's module, `dependents = cg.dependents_of(module)`.
4. **Order fewest-dependents-first** (ascending `len(dependents)`); tie-break by `score` desc (high-value, low-risk first). Assign `order = 0,1,2,…`.
5. Build `Plan{repo, dominant_finding, tasks=[PlanTask{file, opportunities, dependents, order}], confirmed=False}`; `storage.save_plan(plan.to_dict())`; return it.

**Why fewest-dependents-first:** editing a low-blast-radius file first means later edits land on already-stable ground; editing a heavily-depended-on module first risks having to re-touch it as dependents shift. (Matches roadmap §1.)

**Honest scope:** ordering quality is bounded by the call graph's resolver (best-effort — dynamic dispatch, `getattr`, re-exports are invisible). The plan is an *aid*, not a proof; the human confirm step and the per-edit gate stack are what make it safe. State this in the rendered plan.

## 5. `confirm_plan` — the human checkpoint

```python
confirm_plan(decision="approve", order: list[str] | None = None) -> dict
```
- `decision="approve"` → load the current plan, set `confirmed=True`, `decision="approve"`, `save_plan`, return it. This is the green light for execution.
- `decision="reject"` → set `confirmed=False`, `decision="reject"`; the campaign stops.
- `decision="reorder"` with `order=[file,...]` → reorder `tasks` to the human's sequence, set `confirmed=True`, `decision="reorder"`. (Lets a human override the dependency heuristic.)
- Records the decision; **never changes code.** Returns the (possibly reordered) confirmed plan for the dashboard.

## 6. Schema & storage (detail)

Dataclasses per §A.2 in `core/schema.py`, all with `to_dict()`; `Plan`/`PlanTask`/`Opportunity` also need `from_dict()` so `load_plan()` rehydrates a real `Plan` (or the tools can operate on the dict — pick one; recommend rehydrating for `confirm_plan`'s mutation). Storage per §A.3: one current plan under `refactorika:plan` (Redis) / `"plan"` (JSON), Redis-primary with JSON fallback exactly like `append_log`.

## 7. Dashboard / the visible story (the demo multiplier)

`render_campaign(audit_before, plan, log, audit_after)` prints, in order:
- **AUDIT** — the ranked file table, by-kind totals, the headline `dominant_finding`.
- **PLAN** — tasks in execution order: `#order · file · N opportunities · M dependents`, with a `CONFIRMED ✓` / `UNCONFIRMED` banner reflecting `confirm_plan`.
- **EXECUTION** — the existing edit-log render (gate PASS/FAIL per task, commits, rollbacks, re-proposes) — reuse the current `render(log)`.
- **HEALTH** — one closing line: `opportunities 5 → 1 (−80%) · avg nesting 4.1 → 2.3 · files improved 2/2`, computed from `audit_before` vs `audit_after` (re-run `audit_repo` after the campaign).

This is the close: a human watched a ranked plan, approved it, and saw every edit verified, with a measured before/after. That's the v3 demo.

## 8. Demo (curated `demo_repo/`)

Extend `scripts/demo.py`: keep the single-file good/bad moment, then run the campaign — `audit_repo → get_plan → confirm_plan("approve") → apply the planned edits via the existing verified path → render_campaign`. Runs offline. The planted repo already has enough (orders.py nesting + dup imports; billing.py) to produce a ≥2-task plan; add a second deviating file if a longer plan demos better.

## 9. Testing (`tests/`)

Match the existing fast, offline, tmp_path style:
- `test_audit.py` — aggregation counts, `by_kind`, `dominant_finding`, entries sorted by score; empty repo → empty audit.
- `test_plan.py` — fewest-dependents-first ordering on a tiny 3-file graph where B and C import A (A has most dependents → ordered last); score tie-break; plan persisted + reloadable.
- `test_confirm.py` — approve sets `confirmed=True`; reject; reorder applies a custom order; round-trips through `save_plan`/`load_plan` (JSON backend).
- `test_call_graph.py` (extend) — `dependents_of` returns the right modules; `context.py` still passes after the refactor.

## 10. Build order

1. **§A.0 CallGraph reconcile** — add `dependents_of`/`dependent_count`, DRY `context.py`. (Unblocks planning; smallest.)
2. **§A.2 schema + §A.3 storage** — freeze `RepoAudit`/`Plan`/`PlanTask` and `save_plan`/`load_plan`.
3. **§A.4 `audit.py` + §A.1 `audit_repo` tool** — repo report (demo-able alone).
4. **§A.1 `get_plan` + `confirm_plan` tools** — ordering + the human gate.
5. **§A.5 dashboard + §A.6 demo** — the visible campaign + before/after health.

Each step is independently demoable; the audit report alone is a worthwhile increment.

## 11. Open decisions

1. **Plan representation in tools** — operate on dicts, or rehydrate `Plan` via `from_dict`? (Recommend rehydrate so `confirm_plan` mutates a typed object.)
2. **Should v3 add a thin `next_task()` helper** that returns the next unexecuted confirmed task, or leave Claude to walk `plan.tasks` itself? (Recommend leave it to Claude — fewer moving parts.)
3. **`dominant_finding` definition** — highest summed-rank kind (current spec) vs single highest-rank opportunity. (Recommend summed-rank kind — "the thing most worth doing across the repo.")
4. **Plan retention** — single current plan (this spec) vs a history of plans. (Recommend single, overwritten — hackathon simplicity.)
5. **Consolidate the duplicate `_collect_py_files`** (v2-review R15) now that two consumers need it, or leave it. (Recommend consolidate into `parser.py` while adding audit.)
</content>
