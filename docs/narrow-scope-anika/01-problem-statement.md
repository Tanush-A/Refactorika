# Problem Statement

## The Problem

As Python codebases grow, they accumulate technical debt in two forms:

1. **Poor organization** — logic that belongs in separate modules ends up in a single file, imports are scattered and redundant, and functions grow to handle too many concerns.
2. **Rising complexity** — deeply nested conditionals, long functions, and tangled control flow make code hard to read, test, and maintain.

These issues don't appear overnight. They build gradually, and by the time they're painful, the effort to fix them manually is high enough that teams defer the work indefinitely.

## Why Existing Tools Fall Short

Static linters like `ruff` and `flake8` flag style violations but don't restructure code. Type checkers like `pyright` surface type errors but don't reorganize modules. Both require the developer to manually interpret findings and apply fixes — they assist with *what's wrong*, not *how to fix it structurally*.

AI assistants can suggest refactors, but they operate in a chat interface disconnected from the actual file system and require developers to manually copy, paste, and apply changes.

## How Refactorika Fits In

Refactorika is an MCP (Model Context Protocol) server that exposes refactoring capabilities directly to Claude. Because it runs as an MCP tool, Claude can call it inline during a conversation — reading files, analyzing structure, applying changes, and verifying the result — without leaving the development workflow.

The goal is to make structural refactoring as frictionless as running a linter: point it at a codebase, describe the intent, and get clean, reorganized code back.
