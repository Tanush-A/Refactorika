# Scope

## v1 — Initial Scope

Refactorika v1 targets **simple Python codebases**: single-package projects or small multi-file scripts where the structure is shallow and the logic is self-contained.

### In Scope

**Organization improvements**
- Splitting large files into logically grouped modules
- Reordering and deduplicating imports (stdlib → third-party → local)
- Extracting reusable helper functions from bloated call sites

**Complexity reductions**
- Breaking up long functions into smaller, named units
- Flattening deeply nested conditionals (early returns, guard clauses)
- Replacing repetitive code blocks with extracted, parameterized functions

### Out of Scope (v1)

- Multi-language support (JavaScript, TypeScript, Go, etc.)
- Large-scale architectural rewrites (e.g., monolith → microservices)
- Changes that alter runtime behavior or public API contracts
- Test generation or test coverage improvements
- Dependency management or `pyproject.toml` changes

## Future Scope (v2)

The following are committed v2 capabilities, built on Redis Iris for agent memory, vector search, and context retrieval. See [05-v2-features.md](05-v2-features.md) for the full spec.

**Context & Documentation Generation**
- AI-generated, self-updating module documentation — Refactorika reads the codebase and emits `.refactorika/context/<module>.md` files capturing purpose, key exports, and architectural decisions.
- Cross-session Redis agent memory: codebase knowledge persists across runs so context accumulates rather than being re-derived each time.

**Duplicate & Dead Code Detection**
- Semantic duplicate detection via vector search: embed each function's normalized AST signature, store in a Redis vector index, query by cosine similarity to surface functions with the same logic under different names.
- Dead code analysis via graph-based reachability: build a call graph from the AST, mark entry points, BFS/DFS to flag symbols unreachable from production traffic.

**New MCP tools (v2):** `generate_docs(path)`, `find_duplicates(path)`, `find_dead_code(path)`, `get_context_map(path)`.

## Exploratory (beyond v2)

- Support for larger, multi-package Python projects
- Framework-aware refactoring (e.g., Django, FastAPI patterns)
- Additional language targets
