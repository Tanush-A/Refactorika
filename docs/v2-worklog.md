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

## Deferred — intentional `[decide]`/`[tune]` from the spec (⚪ low priority)

Explicitly left open in `v2_spec.md §14`:
- [ ] **F1** Near-exact structural duplicate tier (`SequenceMatcher` ≥ 0.95 over the type stream) — spec §3.1 `[decide]`.
- [ ] **F2** Registration-decorator entry-point list has only 3 entries (`app.route`, `click.command`, `pytest.fixture`) — expand (spec §4.2 `[tune list]`).
- [ ] **F3** Method-level reachability (methods currently collapse under their class) — spec §4.1 allowed this for v1.
- [ ] **F4** Tune thresholds (`0.83` semantic cutoff, confidence ranks) against the curated repo (spec §14.7).
</content>
