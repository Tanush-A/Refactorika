# Problem Statement

## The Problem

As Python codebases grow, they accumulate technical debt in four compounding forms:

1. **Poor organization** — logic that belongs in separate modules ends up in a single file, imports are scattered and redundant, and functions grow to handle too many concerns.
2. **Rising complexity** — deeply nested conditionals, long functions, and tangled control flow make code hard to read, test, and maintain.
3. **Context and documentation rot** — the intent behind architectural decisions evaporates as teams grow or engineers leave. Documentation goes stale because humans forget to update it when logic changes. New engineers spend days playing "software archaeologist" — using `git blame` to figure out why a bizarre workaround exists, terrified that changing it will break a silent dependency. There is no tool that keeps the *why* alive alongside the code.
4. **Duplicate and dead code** — when an engineer needs a utility, they often won't realize a coworker already built it in a different directory and write their own version. Over time, codebases accumulate phantom duplicates (the same logic written five different ways) and dead code (functions no longer reachable by production traffic). Fixing a bug in one copy doesn't fix the other four. Bundle size grows, performance degrades, and the codebase becomes progressively harder to reason about.

These issues don't appear overnight. They build gradually, and by the time they're painful, the effort to fix them manually is high enough that teams defer the work indefinitely.

## Why Existing Tools Fall Short

Static linters like `ruff` and `flake8` flag style violations but don't restructure code. Type checkers like `pyright` surface type errors but don't reorganize modules. Both require the developer to manually interpret findings and apply fixes — they assist with *what's wrong*, not *how to fix it structurally*.

AI assistants can suggest refactors, but they operate in a chat interface disconnected from the actual file system and require developers to manually copy, paste, and apply changes. And no existing tool addresses the knowledge-gap problem: as code changes, the context behind *why* it was written that way is lost permanently.

## How Refactorika Fits In

Refactorika is an MCP (Model Context Protocol) server that exposes refactoring capabilities directly to Claude. Because it runs as an MCP tool, Claude can call it inline during a conversation — reading files, analyzing structure, applying changes, and verifying the result — without leaving the development workflow.

The v1 goal is to make structural refactoring as frictionless as running a linter: point it at a codebase, describe the intent, and get clean, reorganized code back.

The v2 goal goes further: use Redis Iris for cross-session agent memory, vector search, and context retrieval to also solve the knowledge-gap problem — detecting semantic duplicates, pruning dead code, and generating self-updating documentation that survives team turnover. See [05-v2-features.md](05-v2-features.md).
