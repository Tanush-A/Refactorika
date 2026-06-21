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

## Future Scope (Exploratory)

- Support for larger, multi-package Python projects
- Framework-aware refactoring (e.g., Django, FastAPI patterns)
- Additional language targets
