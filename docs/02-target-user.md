# Target User & Scope

## Target user

A developer or team with a legacy or partially-migrated codebase — including codebases that have accumulated AI-generated inconsistency — who wants to bring it into a single consistent pattern, and wants an AI agent to do the mechanical work safely rather than doing it by hand or trusting an agent unsupervised.

## Non-goals (v1)

Edit Memory is deliberately narrow for v1, both to ship something demoable and to avoid overclaiming:

- **One convention type.** Scoped to error-handling style (exceptions vs `Result<T>`/explicit error returns vs nullable sentinels) — not general-purpose convention detection across arbitrary pattern types.
- **No IDE-grade "find all usages."** Call-site detection is best-effort (AST/grep-based), not full static type-checking or a true find-all-usages engine.
- **TypeScript only.** No multi-language support in v1.
- **Single-run memory.** No persistent memory across multiple repo lifecycles/sessions in v1 (this is a [Reach] goal — see [06-redis-integration.md](06-redis-integration.md)). v1's memory persists only within the current audit-and-refactor run.

## Why the scope is this narrow

The PRD treats generalization risk as the top risk (see [08-risks-and-scope.md](08-risks-and-scope.md)): convention detection that only works on a curated demo repo, not arbitrary code, is a real failure mode. Staying narrow — one language, one convention type, honest framing about call-site detection being best-effort — is what makes the demo credible rather than overclaimed.
