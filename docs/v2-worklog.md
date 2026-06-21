# v2 Worklog — gaps between the shipped V2 implementation and `v2_spec.md`

> Found by auditing the `V2 implementation` commit (`5d22f2a`) against `docs/v2_spec.md`. None of these are covered by `docs/13-v3-roadmap.md` (that doc is all *new* features — repo-wide audit, call-site-sweep gate, Sentry). The one overlap: v3 §0 plans to (re)build `analysis/call_graph.py`, so the **Bucket B** dead-code fixes can either land here or fold into that work.
>
> **What's solid (not in scope here):** all 8 tools register and run; 45 tests pass; the atomic mutation path (`apply_and_verify` / `apply_and_verify_multi`) is correct — snapshots all files, parse-gates before writing, commits all-or-restores-all. The gaps below are in ranking/heuristics and the memory/context layer, not the trust spine.

## How to use this
Items are grouped into **buckets by the files they touch**, so buckets can be worked **in parallel** without merge conflicts. Within a bucket, do items top-to-bottom. Priority: 🔴 can produce wrong results in a live test · 🟠 a spec feature that's half-wired · 🟡 cosmetic/polish · ⚪ intentionally deferred (`[decide]`/`[tune]` from the spec).

---

## Bucket A — Duplicate detection polish  · files: `analysis/duplicates.py`

- [ ] **A1 🟡 Rank + sort are wrong.** Plain: when it finds duplicate functions, results should be scored 0–100 by how similar they are and listed best-first; instead they're just numbered 1,2,3 in discovery order and never sorted. The duplicates themselves are correct — only the order/score looks arbitrary in a demo.
  - Where: `duplicates.py:172,187,271` (`rank` is a running counter) and `:285-287` (concatenates structural+semantic, no sort).
  - Fix: set `rank = round(similarity * 100)` per pair; sort the combined `pairs` list by `rank` desc before returning.
- [ ] **A2 🟡 Cross-tier dedup is too aggressive.** Plain: the guard that stops the same pair being listed twice also drops a *real* semantic pair (A,C) when A and C each happen to appear in *different* structural pairs.
  - Where: `duplicates.py:262-265` — checks set-membership of each endpoint, not the actual pair.
  - Fix: track emitted structural pairs as `frozenset({a_key, b_key})` and skip a semantic pair only if that exact frozenset was already emitted.

## Bucket B — Dead-code accuracy  · files: `analysis/call_graph.py`, `analysis/dead_code.py`

> Highest live-test risk. Heads-up for whoever's testing: treat `find_dead_code` as advisory and eyeball results before proposing `remove_dead_code`.

- [ ] **B1 🔴 Name resolver invents false call-edges → real dead code looks alive.** Plain: to decide what's dead, it traces who-calls-what. When two files both define `compute`, a bare `compute()` call is credited to whichever it finds first, so a truly-dead function can look "called." Causes false negatives (misses dead code).
  - Where: `call_graph.py:~312` final fallback matches any node whose unqualified name equals the call name; `:~246` `call_sites()` counts across same-named symbols.
  - Fix: only resolve unqualified names within the same module's symbol table or a real imported-name map; when genuinely ambiguous, record no edge (or a flagged "ambiguous" one) rather than guessing the first match.
- [ ] **B2 🔴 "Not sure it's dead" warning fires far too often.** Plain: if a function's *name* shows up inside any string or comment, it lowers confidence to `low` (in case the code calls it dynamically). But it matches every word in every string, so tons of symbols get wrongly demoted.
  - Where: `dead_code.py:~147` regex matches any identifier-like substring in any string literal.
  - Fix: narrow to actual reflection patterns (e.g. `getattr(obj, "name")`, string keys passed to dispatch), not all strings/comments.
- [ ] **B3 🔴 `__all__` / `__main__` parsed by crude regex → missed entry points.** Plain: these are how code says "this is a real entry point, don't call it dead." It eyeballs the text instead of parsing, so tuple `__all__ = (...)`, multi-line lists, and multi-line `__main__` calls slip past → those entry points get mis-flagged.
  - Where: `call_graph.py:63` (`__all__` list-only regex), `:77-85` (`__main__` call regex).
  - Fix: read both via tree-sitter (the AST is already parsed) instead of regex over source text.
- [ ] **B4 🟡 `storage` param is accepted but unused** in `find_dead_code` (`dead_code.py:25-26`) — wire the AST-signature cache or drop the param. (Low priority; note it's also a v3 §0 concern.)

## Bucket C — Memory & docs layer  · files: `memory/context.py`, `docs_gen.py`, `core/schema.py`

> This is where the "smart memory" is half-wired — it falls back to dumb heuristics instead of the AI-similarity path.

- [ ] **C1 🟠 Vector ("find related modules by meaning") never actually runs.** Plain: the pitch is to surface the most *related* files via AI similarity. But nothing ever feeds the per-module notes into the similarity index — only individual *functions* get indexed (by duplicate detection). So `relevant()` queries an index with no module entries, finds nothing, and silently falls back to "files whose names start the same."
  - Where: only `duplicates.py:226` ever calls `vector_index.upsert(...)`, with function meta `{file,name,line}` and no `module` key; `context.py:32-48` reads `meta["module"]` (always empty).
  - Fix: in `generate_docs`/agent-memory, embed each `ModuleContext` summary and `upsert` it with `meta={"module": ...}` so the vector path has data to find.
- [ ] **C2 🟠 "Which files depend on this?" ignores the call graph.** Plain: there's a real tool that can read the code and find dependents, but the docs feature instead asks its own memory "did I previously record a dependency?" On a fresh repo it has no memory yet, so everything reports `dependents: []`.
  - Where: `context.py:76-79` (`dependents()` reads stored `ctx.dependents`, inverted/circular); `docs_gen.py:54` consumes it. `analysis/call_graph.py` exists but is orphaned here.
  - Fix: compute dependents from the call graph (`dependents_of`) at doc-gen time. (Note: v3 §0 will provide `dependents_of`; could share.)
- [ ] **C3 🟠 `get_context_map` missing `last_updated_run`.** Plain: the "what do you remember about this file" response was supposed to say *when it last looked*; that field just isn't there.
  - Where: not in `ModuleContext` (`schema.py:146-177`) nor the return (`docs_gen.py:132-137`). Spec §2.4.
  - Fix: stamp a run id/timestamp on `ModuleContext` when persisting and include it in the return. (Timestamps in this repo come from outside the call — pass it in, don't call `Date.now()`-equivalents in pure code.)
- [ ] **C4 🟡 Generated docs mix facts and fill-in blanks.** Plain: the plan was for the tool to write the *facts* and leave clearly-marked blanks for Claude to write the prose; instead facts and `<!-- claude: fill -->` placeholders are interleaved, so it's unclear what's real vs. to-be-filled.
  - Where: `docs_gen.py:~76-92` template. Spec §8.
  - Fix: separate an "extracted facts" section from a clearly-marked "needs Claude" section.
- [ ] **C5 🟡 Magic-number flag is noise.** Plain: trying to spot magic numbers, it flags any 2+ digit number — years (`2024`), versions, etc.
  - Where: `docs_gen.py:69`.
  - Fix: tighten the heuristic (skip comments/strings; ignore common years/versions) or drop it.

## Bucket D — Repo hygiene & demo  · files: `.gitignore`, `scripts/demo.py`, junk files, legacy tests

- [ ] **D1 🟡 Demo script doesn't show any V2 feature.** Plain: `scripts/demo.py` still only does the original good-edit/bad-edit refactor. There's no scripted "watch it find a duplicate and safely merge it" or "find + safely remove dead code" moment — the thing a live demo most needs.
  - Fix: extend `scripts/demo.py` to call `find_duplicates` (on the planted `demo_repo/billing.py` duplicate) and `find_dead_code`, then drive a `consolidate_duplicate` / `remove_dead_code` through `apply_and_verify(_multi)`.
- [ ] **D2 🟡 Worktree junk committed.** Two `.claude/worktrees/agent-…` files got committed in `5d22f2a`. Remove them and add `.claude/worktrees/` to `.gitignore`.
- [ ] **D3 ⚪ Legacy placeholder tests.** `tests/test_extract|flatten|imports|split_file.py` are still `pytest.skip` stubs from the old `src/` layout. Delete (the real coverage is in `test_duplicates`/`test_dead_code`/etc.).

## Bucket E — Multi-file edit log  · files: `core/apply.py`, `core/storage.py`

- [ ] **E1 🟡 `retries` only counts the first file** on a multi-file edit (`apply.py:~54` + `storage.count_attempts` filters by the first path only). Informational only — doesn't affect gating. Fix: count attempts across any touched file, or document the limitation.

## Deferred — intentional `[decide]`/`[tune]` from the spec (⚪ low priority)

These were explicitly left open in `v2_spec.md §14`; logging so they're not forgotten:
- [ ] **F1** Near-exact structural duplicate tier (`SequenceMatcher` ≥ 0.95 over the type stream) — not implemented (spec §3.1, `[decide]`).
- [ ] **F2** Registration-decorator entry-point list has only 3 entries (`app.route`, `click.command`, `pytest.fixture`) — expand (spec §4.2, `[tune list]`).
- [ ] **F3** Method-level reachability (methods currently collapse under their class) — spec §4.1 allowed this for v1.
- [ ] **F4** Tune thresholds (`0.83` semantic cutoff, confidence ranks) against the curated repo (spec §14.7).

---

## Suggested parallelization

Buckets **A, B, C, D, E touch disjoint files** → safe to run concurrently. Recommended split if fanning out:
- **Agent 1:** Bucket B (dead-code accuracy — biggest correctness win).
- **Agent 2:** Bucket C (memory/docs wiring).
- **Agent 3:** Buckets A + E (small, duplicates + edit-log).
- **Agent 4:** Bucket D (demo + hygiene — most demo value).

`schema.py` is touched only by C3 — keep it inside Bucket C to avoid a shared-file conflict.
</content>
