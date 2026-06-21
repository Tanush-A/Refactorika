# Problem Statement

## The Problem

As Python codebases grow, they accumulate technical debt in four compounding forms:

1. **Poor organization** — logic that belongs in separate modules ends up in a single file, imports are scattered and redundant, and functions grow to handle too many concerns.
2. **Rising complexity** — deeply nested conditionals, long functions, and tangled control flow make code hard to read, test, and maintain.
3. **Context and documentation rot** — the intent behind architectural decisions evaporates as teams grow or engineers leave. Documentation goes stale because humans forget to update it when logic changes. New engineers spend days playing "software archaeologist" — using `git blame` to figure out why a bizarre workaround exists, terrified that changing it will break a silent dependency. There is no tool that keeps the *why* alive alongside the code.
4. **Duplicate and dead code** — when an engineer needs a utility, they often won't realize a coworker already built it in a different directory and write their own version. Over time, codebases accumulate phantom duplicates (the same logic written five different ways) and dead code (functions no longer reachable from any entry point). Fixing a bug in one copy doesn't fix the other four. Bundle size grows, performance degrades, and the codebase becomes progressively harder to reason about.

These issues don't appear overnight. They build gradually, and by the time they're painful, the effort to fix them manually is high enough that teams defer the work indefinitely.

## Why Existing Tools Fall Short

Static linters like `ruff` and `flake8` flag style violations but don't restructure code. Type checkers like `pyright` surface type errors but don't reorganize modules. Both tell you *what's wrong*, not *how to fix it structurally*, and neither can touch problems 3 and 4 at all.

AI assistants can suggest refactors, but they operate in a chat window disconnected from the file system — copy/paste hell — and they have **no memory**: every session starts from zero, re-deriving the same structure, forgetting why the last change was made. Nothing keeps the *why* alive as the code changes, and nothing **proves** a suggested change is safe before it lands.

## How Refactorika Fits In

**Refactorika is a graph-driven, verified refactoring engine.** It treats refactoring as a
whole-program graph problem, not a per-file one — because the dangerous changes (rename, move,
dedup, dead-code removal) are about *relationships between files*. It provides four things a chat
assistant or a linter cannot:

1. **A reference-correct program model** — a symbol graph built from real static analysis (Jedi),
   so it knows the true binding of every name. A rename updates *every* real reference and
   *nothing* that merely shares the name; dead code is flagged only when reachability proves it
   unreachable from any entry point.
2. **Deterministic transform engines** — rope (cross-file rename), LibCST (node replacement,
   dead-code removal), ruff + autoflake (cleanup) do the actual edits reference-correctly across
   every file. The LLM is used only for *judgment* (which god function to split, how to name the
   pieces) and emits compact specs, never hand-written diffs.
3. **A verification gate, then commit** — every edit passes `parse → ruff → pyright → pytest`
   (tests *impact-scoped* to what the change can affect) before `git commit`, and reverts
   byte-for-byte on any failure. The full suite gates the run at baseline and finale. The promise:
   **the engine restructured it, but nothing landed unverified.**
4. **A shared brain (Redis Iris)** — the graph, the per-decision memory, and a vector index live
   in Redis (local-JSON fallback). Decisions are recalled so the work stays *consistent* across
   the repo — the same situation is refactored the same way every time.

It runs as a **standalone CLI** (`refactorika <dir>`) and as an **MCP server** for use inside an
agent. The goal: make safe, repo-wide structural change as frictionless as running a linter.

See [02-scope.md](02-scope.md) for what's in and out, and **[v3_spec.md](v3_spec.md)** for the
full as-built architecture, the verification model, and the Redis-as-decision-memory design.

</invoke>
