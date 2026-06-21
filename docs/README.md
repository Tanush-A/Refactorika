# Refactorika — Documentation

> **Refactorika** is a **graph-driven, verified refactoring engine** for Python. Point it at a
> repo; it builds a reference-correct whole-program model, plans a safe dependency order, applies
> deterministic transforms, and proves nothing broke — committing each verified edit and reverting
> anything that fails its tests. It ships as a standalone CLI (primary) and an MCP server (secondary).

This is the documentation index. It is written so a developer new to the project can understand
**every** subsystem — what it does, how it is wired, and where the code lives. Current as of
package version `0.2.0`.

---

> **⚠ Two branches.** `working` is the **demo** (MCP agent harness + four-arm benchmark); `main` is the
> **v3 graph engine**. They have different code. **Read [branches.md](branches.md) first.**

## Start here

| If you want to… | Read |
|---|---|
| Understand the two branches (do this first) | [branches.md](branches.md) |
| Understand the product and the thesis in 5 minutes | [`../CLAUDE.md`](../CLAUDE.md) and [`../README.md`](../README.md) |
| Understand the whole architecture and data flow | [architecture.md](architecture.md) |
| Find what any module/function does | [module-reference.md](module-reference.md) |
| Run it: CLI + MCP for both branches | [cli-and-mcp.md](cli-and-mcp.md) |
| Configure it / env vars / Make targets | [configuration.md](configuration.md) |
| Understand the agent campaign + language adapters | [agents-and-languages.md](agents-and-languages.md) |
| Understand the benchmarks (incl. the four-arm study) | [evaluation.md](evaluation.md) |
| Understand the test suite | [testing.md](testing.md) |
| The polished product story | [devpost.md](devpost.md) |

---

## The living docs (kept current with the code)

| Doc | Covers |
|---|---|
| [branches.md](branches.md) | **The map of the two branches** — what code is on `working` vs `main`, how shared files diverge, which to use. |
| [architecture.md](architecture.md) | The layers, the verified spine, ordering rules, data flow, front doors — branch-tagged. The canonical architecture reference. |
| [module-reference.md](module-reference.md) | File-by-file reference for `refactorika/`, each entry tagged [both]/[main]/[working]/[diverged]. Signatures, contracts, side effects. |
| [cli-and-mcp.md](cli-and-mcp.md) | Exact CLI + MCP surface for **both** branches (working `scan`/`fix`/`serve` + 13 tools; main engine CLI + its tools). |
| [agents-and-languages.md](agents-and-languages.md) | The `agents/` specialist campaign (live vs stub per branch) and the `languages/` adapter/registry layer. |
| [configuration.md](configuration.md) | Every environment variable, dependency map, `Makefile` targets (both branches), Docker, storage/offline behavior. |
| [evaluation.md](evaluation.md) | RefactorBench adapter, full-system benchmark, and the **four-arm agent benchmark** (RAW / HARNESS / AGENTIC RAW / AGENTIC HARNESS) with published numbers + reproduction. |
| [testing.md](testing.md) | The offline test suites (main 264, working 202) — what each file covers and how to run. |
| [usage.md](usage.md) | Task-oriented usage guide for the CLI and MCP server. |
| [v3_spec.md](v3_spec.md) | The condensed product spec / "source of truth" narrative. |
| [01-problem-statement.md](01-problem-statement.md), [02-scope.md](02-scope.md), [03-tech-stack.md](03-tech-stack.md) | Conceptual framing: the problem, what's in/out of scope, the technology choices. |
| [semantic_index_design.md](semantic_index_design.md) | Design notes for the semantic codebase index. |
| [pipeline.md](pipeline.md) | Deep narrative on the transform/checker pipeline. |

## Historical / frozen docs (a record of how we got here — not maintained)

These describe earlier designs or process logs. They are preserved for context and carry a
"historical" banner; do **not** treat them as current.

- [v2_spec.md](v2_spec.md) — the superseded MCP-harness spec (pre-graph engine).
- [v2-worklog.md](v2-worklog.md), [v3-worklog.md](v3-worklog.md) — dated build logs.
- [refatorika_plan.md](refatorika_plan.md) — the original design rationale.
- [04-architecture.md](04-architecture.md), [05-redis-iris.md](05-redis-iris.md) — early stubs, superseded by [architecture.md](architecture.md) and [configuration.md](configuration.md).
- [11-benchmarks-and-eval.md](11-benchmarks-and-eval.md), [12-benchmark-display-spec.md](12-benchmark-display-spec.md), [12-harness-benchmark.md](12-harness-benchmark.md), [13-full-system-benchmark.md](13-full-system-benchmark.md), [13-v3-roadmap.md](13-v3-roadmap.md), [14-benchmark-case-catalog-and-stress-plan.md](14-benchmark-case-catalog-and-stress-plan.md), [15-four-arm-agent-benchmark-contract.md](15-four-arm-agent-benchmark-contract.md) — benchmark planning docs, superseded for usage by [evaluation.md](evaluation.md).

---

## One-paragraph mental model

A refactor is a **whole-program graph problem**. The **LLM** brings judgment (which god function
to split, how to name the pieces); **deterministic engines** (rope, LibCST, ruff/autoflake) bring
correctness at scale; the **graph** (built by Jedi) connects them with real name binding; and the
**test suite** proves behavior is preserved. **Redis** remembers every judgment so near-duplicate
code is refactored consistently across the repo — and everything degrades to local JSON when Redis
or a model is unreachable, so the engine never *depends* on either.
