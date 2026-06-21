# Problem, Solution & Purpose

## Problem

Refactoring a pre-existing codebase has two compounding problems today:

1. **No one knows how inconsistent the codebase already is.** Different files, written at different times or by different people (or by different AI tools), follow different conventions — error handling, naming, structure. There's no tool that surfaces this *before* a refactor starts; teams find out by accident, mid-PR.
2. **AI coding agents make it worse, not better, at scale.** An agent refactoring file-by-file has no persistent sense of "what convention did we just establish two files ago," and no systematic way to check whether a change breaks call sites elsewhere in the repo. Existing tools (Cursor, Copilot, Aider) rely on static, human-written rules files (`.cursorrules`, `CLAUDE.md`) that don't reflect the codebase's actual current state and don't update themselves.

This problem is getting more urgent, not less: a growing share of production code today is AI-generated, written quickly and inconsistently across sessions, without a unifying convention. That's "AI slop" — code that works but accumulates inconsistency invisibly, and isn't fit to ship or maintain as-is. Edit Memory exists to make that code safe to bring back under one convention before it goes further into production.

## Solution

**Edit Memory** is a convention-audit and guided-refactor layer designed to be run *as a plugin* against a codebase that's ready for refactor. Instead of trusting an agent to refactor unsupervised, or doing the work by hand, a team runs Edit Memory on the repo and it:

- **Audits** the codebase for a specific convention (v1: error-handling style) and reports where it's inconsistent.
- **Plans** a safe, dependency-aware order to fix it (least-risky files first).
- **Guides** an agent through the refactor, checking each edit against the target convention and flagging any missed call sites — without dumping the entire repo into context at every step.
- **Persists** the result as context files (structural maps of the refactored codebase) so future developers and agents can understand it without re-deriving the structure.

The core idea: convert refactoring from an unsupervised, repo-wide agent task (high blast radius, no guardrails) into a supervised, file-by-file pipeline with automated verification gates at every step.

## Purpose / why this matters

- **Plugin model, not a one-off script.** Edit Memory is meant to be invoked on a codebase once it's identified as "ready for refactor" — it's a tool you run *on top of* an existing repo, via MCP (so it plugs into Claude Code, Cursor, etc.) or a CLI fallback, not a rewrite-from-scratch generator.
- **Targets the AI-slop problem directly.** As more production code is AI-generated, the failure mode shifts from "bugs" to "inconsistency that compounds" — every new agent session might pick a different error-handling style, a different naming scheme, with no memory of what came before. Edit Memory gives the refactor pass exactly that memory.
- **Trust through verification, not trust through prompting.** Rather than asking an agent to "please be careful," every edit passes through a verification harness (parse check, typecheck, call-site sweep) before being committed. The pitch is "an agent did this work, but every step was checked" — not "an agent did this work, trust it."

See [02-target-user.md](02-target-user.md) for who this is built for, and [05-core-components.md](05-core-components.md) for how the pipeline works mechanically.
