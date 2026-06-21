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

**Refactorika is an agent harness delivered as an MCP (Model Context Protocol) server.** Claude is the reasoning agent — it reads code, decides what to change, and proposes concrete edits. Refactorika is the harness around it, providing three things Claude can't get on its own:

1. **Structure-aware analysis** — read-only tools that parse the AST and surface ranked, concrete opportunities (god-files, deep nesting, semantic duplicates, dead code) instead of vague advice.
2. **A verification gate stack** — every *mutation* Claude proposes is run through `parse → ruff → pyright → pytest` and committed only if it passes, rolled back atomically if it doesn't. The product's promise: **the agent restructured it, but nothing landed unverified.**
3. **Cross-session memory (Redis Iris)** — an AST cache, a vector index of every function, long-term agent memory, and a context retriever. Knowledge *compounds*: the second run on a repo is smarter than the first, and the context behind a decision survives team turnover.

Because it runs as an MCP server, all of this happens inline in a normal Claude conversation — read, analyze, propose, verify, commit, remember — without leaving the development workflow. The goal is to make safe structural change as frictionless as running a linter: point it at a codebase, describe the intent, and get clean, reorganized, **proven-safe** code back — plus living documentation of why it looks the way it does.

See [02-scope.md](02-scope.md) for what's in and out, [04-architecture.md](04-architecture.md) for how the harness is built, and [05-redis-iris.md](05-redis-iris.md) for the memory layer.
</content>
</invoke>
