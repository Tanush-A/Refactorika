# v2 Worklog — gaps between the shipped V2 implementation and `v2_spec.md`

> **STATUS (commit `ca39786`): all buckets A–E done.** Fixed in parallel (Buckets B/C/D via worktree agents, A/E by hand), integrated onto `narrow-scope-anika`, 52 tests passing, demo runs end-to-end showing find_duplicates / find_dead_code / generate_docs. Only the intentionally-deferred `[decide]/[tune]` items (F1–F4) and one newly-discovered issue (G1) remain — both for v3.
>
> Found by auditing the `V2 implementation` commit (`5d22f2a`) against `docs/v2_spec.md`. None of these are covered by `docs/13-v3-roadmap.md` (that doc is all *new* features — repo-wide audit, call-site-sweep gate, Sentry). The one overlap: v3 §0 plans to (re)build `analysis/call_graph.py`, so the **Bucket B** dead-code fixes landed here and can be carried into that work.
>
> **What was solid (never in scope here):** all 8 tools register and run; the atomic mutation path (`apply_and_verify` / `apply_and_verify_multi`) is correct — snapshots all files, parse-gates before writing, commits all-or-restores-all. The gaps below were in ranking/heuristics and the memory/context layer, not the trust spine.

## How to use this
Items are grouped into **buckets by the files they touch**. Priority: 🔴 can produce wrong results in a live test · 🟠 a spec feature that's half-wired · 🟡 cosmetic/polish · ⚪ intentionally deferred (`[decide]`/`[tune]` from the spec).

---

## Bucket A — Duplicate detection polish  · files: `analysis/duplicates.py`  ✅ done

- [x] **A1 🟡 Rank + sort are wrong.** Plain: when it finds duplicate functions, results should be scored 0–100 by how similar they are and listed best-first; instead they're just numbered 1,2,3 in discovery order and never sorted. The duplicates themselves are correct — only the order/score looks arbitrary in a demo.
  - Fixed: `rank = round(similarity * 100)` per pair (structural = 100); combined list sorted by `rank` desc before returning.
- [x] **A2 🟡 Cross-tier dedup is too aggressive.** Plain: the guard that stops the same pair being listed twice also drops a *real* semantic pair (A,C) when A and C each happen to appear in *different* structural pairs.
  - Fixed: track emitted structural pairs as `frozenset({a_key, b_key})`; skip a semantic pair only if that exact frozenset was already emitted.

## Bucket B — Dead-code accuracy  · files: `analysis/call_graph.py`, `analysis/dead_code.py`  ✅ done

> Was the highest live-test risk. Still: treat `find_dead_code` as advisory and eyeball results before proposing `remove_dead_code` (best-effort static analysis can't see everything).

- [x] **B1 🔴 Name resolver invents false call-edges → real dead code looks alive.** Plain: to decide what's dead, it traces who-calls-what. When two files both define `compute`, a bare `compute()` call is credited to whichever it finds first, so a truly-dead function can look "called."
  - Fixed: unqualified names resolve only within the same module's symbol table, then a real imported-name map, then a project-wide match **only when unambiguous**; ambiguous names record no edge. `call_sites()` counts exact qualnames only. New test: a same-named symbol in another file no longer masks dead code.
- [x] **B2 🔴 "Not sure it's dead" warning fires far too often.** Plain: if a function's *name* shows up inside any string or comment, it lowered confidence to `low`. But it matched every word in every string.
  - Fixed: narrowed to genuine reflection sites — string args to `getattr`/`setattr`/`hasattr`/`delattr` and string keys in dispatch dicts — via AST, not blanket string scan.
- [x] **B3 🔴 `__all__` / `__main__` parsed by crude regex → missed entry points.**
  - Fixed: both read via tree-sitter over the already-parsed AST; handles tuple/set `__all__`, multi-line lists, and multi-line `__main__` blocks.
- [x] **B4 🟡 `storage` param accepted but unused** in `find_dead_code`.
  - Fixed: wired the AST-signature cache (`cache_get`/`cache_set`) keyed on a signature of the directory's files; re-run on unchanged tree skips rebuilding the graph. *(See G1 — this cache shares the absolute-path issue.)*

## Bucket C — Memory & docs layer  · files: `memory/context.py`, `docs_gen.py`, `core/schema.py`, `memory/agent_memory.py`  ✅ done

> This is where the "smart memory" was half-wired — it fell back to dumb heuristics instead of the AI-similarity path.

- [x] **C1 🟠 Vector ("find related modules by meaning") never actually ran.** Plain: nothing fed the per-module notes into the similarity index — only individual functions got indexed.
  - Fixed: `generate_docs` now embeds each `ModuleContext` summary and upserts it with `meta={"module": ...}` (guarded on `[semantic]` availability); `relevant()` now finds module entries. Verified offline with a stub embedder.
- [x] **C2 🟠 "Which files depend on this?" ignored the call graph.**
  - Fixed: `dependents()` builds the call graph and finds modules that actually reference the target; `generate_docs` passes the repo root so it works on a fresh repo.
- [x] **C3 🟠 `get_context_map` missing `last_updated_run`.**
  - Fixed: added a deterministic `last_updated_run` (`run-1`, `run-2`, … off the prior stamp — no wall-clock in pure code) on `ModuleContext`; returned by `get_context_map`.
- [x] **C4 🟡 Generated docs mixed facts and fill-in blanks.**
  - Fixed: the `.md` now has a clearly-marked "Extracted (facts)" section and a separate "Needs Claude" section for prose.
- [x] **C5 🟡 Magic-number flag was noise.**
  - Fixed: strips comments/strings, ignores years (1900–2099) and version-like dotted numbers; flags only bare standalone integers.

## Bucket D — Repo hygiene & demo  · files: `.gitignore`, `scripts/demo.py`, junk files, legacy tests  ✅ done

- [x] **D1 🟡 Demo script didn't show any V2 feature.**
  - Fixed: `scripts/demo.py` now walks ANALYZE → FIND_DUPLICATES → FIND_DEAD_CODE → GENERATE_DOCS → good-edit (commits) → bad-edit (caught + rolled back) → dashboard. Runs without the `[semantic]` extra (prints the "semantic: unavailable" note instead of requiring torch).
- [x] **D2 🟡 Worktree junk committed.** Removed the two `.claude/worktrees/agent-…` files; added `.claude/worktrees/` to `.gitignore`.
- [x] **D3 ⚪ Legacy placeholder tests.** Deleted the four `tests/test_extract|flatten|imports|split_file.py` stubs (suite now has 0 skips).

## Bucket E — Multi-file edit log  · files: `core/apply.py`, `core/storage.py`  ✅ done

- [x] **E1 🟡 `retries` only counted the first file** on a multi-file edit.
  - Fixed: `count_attempts` now accepts a list and counts prior non-committed attempts touching **any** affected file; single-file behavior unchanged.

## Bucket G — discovered during integration (for v3) ⚠️ open

- [ ] **G1 🟠 Analysis/vector cache stores ABSOLUTE file paths.** Plain: the cache key is the file's *content* (good — re-seen code skips re-analysis), but the cached *result* embeds the absolute path it was first analyzed under. So a cache hit from a different working directory (or another machine, or a worktree) hands back a stale path. Symptom seen live: the demo briefly printed `…/.claude/worktrees/agent-…/demo_repo/orders.py` because Redis still held a result cached while an agent ran the demo inside its worktree. Not a correctness bug for the analysis itself, but the path field is wrong/misleading and leaks across checkouts.
  - Where: `core/analyze.py` caches `AnalysisResult` (with absolute `file`/`location`) keyed on content sha1; same pattern in `dead_code.py`'s new cache (B4) and the vector index meta.
  - Fix: store **repo-relative** paths in cached results (and the `meta`), resolving to absolute only at the boundary; or include the path in the cache key. Flushed Redis (`refactorika:cache`, `refactorika:vectors`) as a stopgap.

## Review findings (`/code-review` over the full branch, commit `ca39786`) ⚠️ all open

7-angle review of the whole `refactorika/` package. Verified directly against source. Ranked by severity. These are NEW (separate from A–G above).

### 🔴 Critical — behavior/data loss & broken atomicity

- [ ] **R1 🔴 `transforms/imports.py:89` — `reorder_imports` silently DELETES non-import code between the first and last import.** Plain: it rebuilds the file as `everything-before-first-import + sorted-imports + everything-after-last-import`. Anything sitting *among* the imports that isn't an import — a `LOG = logging.getLogger(...)`, an `if TYPE_CHECKING:` block, `__all__ = [...]`, a conditional/lazy import, comments — gets dropped. The code comment even falsely claims it's preserved. This is the cardinal "changes behavior, not just shape" sin, and it can pass parse+type+(untested)pytest and land.
  - Fix: replace only the exact import-statement spans (or move them), preserving every non-import node in the region; or only collapse a contiguous run of imports with nothing between them.
- [ ] **R2 🔴 `core/apply.py:49` — the snapshot crashes on any new/missing file, bypassing the gate stack.** `originals = {p: rp.read_text()}` runs before the try and before any `EditRecord`. For `split_module` (creating a new module — in scope) the path doesn't exist → `FileNotFoundError` escapes uncaught: no record, no rollback. And rollback writes `originals[p]` back, so a newly-*created* file can't be removed on rollback. The atomic path can't do create-file refactors at all.
  - Fix: treat missing paths as new files (snapshot = sentinel "did not exist"); on rollback, delete files that didn't exist before; wrap snapshot in the failure path.
- [ ] **R3 🔴 `core/apply.py:77` — the write loop is OUTSIDE the try, so a mid-loop write failure leaves the tree half-edited with no rollback.** Write file A, then B's `write_text` raises (read-only/disk-full/perms) → exception propagates, A stays mutated, `_rollback` never runs.
  - Fix: move the write loop inside the try (and track which files were written so rollback restores exactly those).
- [ ] **R4 🔴 `core/apply.py:107` — `_commit_multi` runs outside the try and never checks git's exit code → records `committed` when nothing committed.** A failing `git commit` (hook rejects, locked index, "nothing to commit") is swallowed; `_finalize(..., "committed", ...)` still marks it green. If `git` raises, it's uncaught after files are written. The dashboard's "committed ✓" can be a lie — inverts the "nothing landed unverified" pitch.
  - Fix: check `returncode` of add+commit; on failure roll back and record `skipped-needs-human` (or `rolled-back`), never `committed`.
- [ ] **R5 🔴 `memory/vector_index.py:147,205` — under Redis the `module` meta key is dropped, so the C1 "related modules" fix is dead on the demo backend.** `upsert` persists only `file/name/line`; the RediSearch schema has no `module` field; `_redis_query` rebuilds meta from those three. So `context.relevant()` reads `meta.get("module")` → always empty under Redis. Works only on JSON fallback — but the demo runs on Redis. **(This means C1 is only half-fixed.)**
  - Fix: add a `module` TextField to the schema and include it in upsert mapping + query meta; or store the full meta dict as a JSON blob field.

### 🟠 Medium — gates & cache correctness

- [ ] **R6 🟠 `core/gates.py:62` — `ruff format` mutates the file *after* parse-gate, so committed bytes ≠ parse-validated bytes ≠ the recorded `diff`.** `record.diff` is the agent's proposed text; the bytes committed are the reformatted version parse_gate never saw. Behavior is still type/test-verified, but the audit trail doesn't match what landed.
  - Fix: format the proposed content in-memory before parse-gating and before building the diff, so one canonical byte-string is gated, recorded, and committed.
- [ ] **R7 🟠 `core/gates.py:96` — `test_gate` treats any non-0/non-5 pytest exit as "behavior failed."** A pre-existing collection/import error (exit 2), internal error (3), or usage error (4) elsewhere makes *every* refactor roll back, mislabeled as the refactor breaking behavior. One broken unrelated test blocks all refactoring.
  - Fix: distinguish exit codes — 0 pass, 1 real failure, 5 skip/no-tests, 2/3/4 → harness error (`None`/skip-and-record, not a behavior failure).
- [ ] **R8 🟠 `core/gates.py:53` — `_ruff_violation_count` returns `0` on empty/malformed ruff output.** A ruff config error or non-JSON banner → `JSONDecodeError` → `0` → lint gate reports "clean" → a real lint regression passes.
  - Fix: distinguish "0 violations" from "couldn't parse ruff output"; on parse failure, skip-and-record (or fail), don't treat as clean.
- [ ] **R9 🟠 `analysis/duplicates.py:158` — the structural-fingerprint cache key `fp:{file}:{name}` is content-blind.** Edit a function's body (same name/file) and re-run: `cache_get` returns the stale sha1, grouping it with old structural twins. Every other cache keys on content hash; this one doesn't. (Same family as G1.)
  - Fix: key on a content/AST signature (sha1 of the canonical type stream or function text), not file+name.
- [ ] **R10 🟠 `core/storage.py:77` (`append_log`) — non-atomic read-modify-write of `state.json`, no lock.** Two concurrent `apply` calls on the JSON backend each read→append→write; the second clobbers the first → a record vanishes, or an interleaved write corrupts the file (then every read raises, killing the offline path). Realistic given the parallel-agent build model.
  - Fix: append via atomic write (tmp file + `os.replace`) and/or a file lock; or use an append-only log file instead of rewriting one JSON blob.

### 🟡 Also noted (lower severity / cleanup / efficiency)

- [ ] **R11 🟠 `core/storage.py count_attempts` (Bucket-E code) uses hard `r["status"]`/`r["file"]`** → `KeyError` aborts `apply` on any malformed/older/externally-seeded log record. Read defensively (`.get`), like `agent_memory.history` does.
- [ ] **R12 🟡 `embeddings.py` reloads the ~90MB SentenceTransformer model per `embed_one` call** (no module-level caching), and `duplicates.py` embeds every function **twice** (upsert loop + query loop) → ~2N model loads. Cache the model in a global; embed once into a dict and reuse; batch via `embed(list)`.
- [ ] **R13 🟡 `embeddings.available()` can return True while `embed()` raises** (model download fails at runtime, or openai selected but sentence-transformers missing) — the runtime failure is a non-`ImportError` that escapes the guard. Make `available()` and the provider choice agree, and wrap runtime load failures.
- [ ] **R14 🟡 `memory/vector_index.py:74` — index name is frozen at `__init__` from `embeddings._PROVIDER/_DIM` defaults (`none`/384), which are only set after the first `embed()`.** With `REFACTORIKA_EMBED=openai` (1536-dim) the index is created at dim 384 → later 1536 vectors mismatch → silent fallback. Resolve provider/dim lazily, after the first embed.
- [ ] **R15 🟡 Reuse: `parser.py` is the shared AST front end, but `analyze.py`, `call_graph.py`, and `transforms/dead.py` re-implement AST walking / symbol-name / call-extraction; `_collect_py_files` exists twice with *different* skip sets** (so duplicates vs dead-code scan different file sets); module/stdlib classification is coded 3× with diverging rules (analyzer vs imports-transform can disagree → a never-converging re-propose loop). Consolidate to one helper each.
- [ ] **R16 🟡 `docs_gen.py` flag detection is substring-over-source:** the `bare except:` rule is dead (never matches real `except:`), and `getattr(` matches inside comments/strings → false data written into context files. Use the AST (dead_code.py already has the real walker).

## Deferred — intentional `[decide]`/`[tune]` from the spec (⚪ low priority)

Explicitly left open in `v2_spec.md §14`:
- [ ] **F1** Near-exact structural duplicate tier (`SequenceMatcher` ≥ 0.95 over the type stream) — spec §3.1 `[decide]`.
- [ ] **F2** Registration-decorator entry-point list has only 3 entries (`app.route`, `click.command`, `pytest.fixture`) — expand (spec §4.2 `[tune list]`).
- [ ] **F3** Method-level reachability (methods currently collapse under their class) — spec §4.1 allowed this for v1.
- [ ] **F4** Tune thresholds (`0.83` semantic cutoff, confidence ranks) against the curated repo (spec §14.7).
</content>
