# Refactorika — Documentation

> **⚠ Two branches.** `working` (**this** branch) is the **demo**: an MCP agent harness + the
> four-arm benchmark. `main` is the **v3 graph-driven engine** (`graph/`, `pipeline/`, `llm/`). They
> have different code. **Read [branches.md](branches.md) first** — it maps exactly what's on each.

## Start here

| If you want to… | Read |
|---|---|
| Understand the two branches (do this first) | [branches.md](branches.md) |
| The polished product story | [devpost.md](devpost.md) |
| Run the demo / benchmarks (this branch) | [`../README.md`](../README.md) and [cli-and-mcp.md](cli-and-mcp.md) |
| Understand the architecture + verified spine | [architecture.md](architecture.md) |
| Find what any module/function does | [module-reference.md](module-reference.md) |
| The CLI + MCP surface (both branches, exact) | [cli-and-mcp.md](cli-and-mcp.md) |
| Configure it / env vars / Make targets | [configuration.md](configuration.md) |
| The agent campaign + language adapters | [agents-and-languages.md](agents-and-languages.md) |
| The benchmarks (the four-arm study) | [evaluation.md](evaluation.md) |
| The test suite | [testing.md](testing.md) |

## Comprehensive reference (branch-aware: tagged [both]/[main]/[working])

- [branches.md](branches.md) — the map of the two branches and how shared files diverge.
- [architecture.md](architecture.md) — layers, the shared verified gate stack, data flow, front doors.
- [module-reference.md](module-reference.md) — file-by-file `refactorika/` reference, signatures + contracts.
- [cli-and-mcp.md](cli-and-mcp.md) — full CLI + MCP surface for both branches.
- [configuration.md](configuration.md) — env vars, deps, Makefile targets, Docker, storage/offline.
- [agents-and-languages.md](agents-and-languages.md) — the specialist agent campaign + language adapters.
- [evaluation.md](evaluation.md) — RefactorBench, full-system + the four-arm benchmark with numbers.
- [testing.md](testing.md) — the offline test suites.

## Demo & product docs (this branch)

- [devpost.md](devpost.md) — the product narrative (inspiration, how it works, benchmarks, stack).
- [pipeline.md](pipeline.md) — the transform/checker pipeline as a reachability map.
- [semantic_index_design.md](semantic_index_design.md) — the semantic codebase-index design.
- [usage.md](usage.md) — usage notes.

## Concept docs

- [01-problem-statement.md](01-problem-statement.md) — the problem and how the harness fits in.
- [02-scope.md](02-scope.md) — in/out scope, the behavior-preserving invariant.
- [03-tech-stack.md](03-tech-stack.md) — Jedi · rope · LibCST · ruff/pyright/pytest · Anthropic · Redis.
- [04-architecture.md](04-architecture.md), [05-redis-iris.md](05-redis-iris.md) — early architecture / Redis notes.

## Benchmark specs & planning

- [11-benchmarks-and-eval.md](11-benchmarks-and-eval.md), [12-benchmark-display-spec.md](12-benchmark-display-spec.md), [12-harness-benchmark.md](12-harness-benchmark.md), [13-full-system-benchmark.md](13-full-system-benchmark.md), [14-benchmark-case-catalog-and-stress-plan.md](14-benchmark-case-catalog-and-stress-plan.md), [15-four-arm-agent-benchmark-contract.md](15-four-arm-agent-benchmark-contract.md) — the benchmark contracts/catalog. (Living usage summary: [evaluation.md](evaluation.md).)

## Design history (not current)

- [refatorika_plan.md](refatorika_plan.md) — the original design rationale.
- [v2_spec.md](v2_spec.md) — earlier MCP-harness spec. [v2-worklog.md](v2-worklog.md), [v3-worklog.md](v3-worklog.md) — build logs.
- [v3_spec.md](v3_spec.md), [13-v3-roadmap.md](13-v3-roadmap.md) — the v3 **engine** spec/roadmap. *(Note: the v3 engine code lives on the `main` branch, not here — see [branches.md](branches.md).)*
