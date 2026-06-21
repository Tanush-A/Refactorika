> **⚠ HISTORICAL — not maintained.** Preserved as a record of how the project evolved. For current docs see [docs/README.md](README.md).

# Architecture

> **Moved.** The architecture of the as-built engine is documented authoritatively in
> **[v3_spec.md](v3_spec.md)** — see §3 (architecture diagram), §4 (module map), §5 (the
> transform contract), §6 (the verification model), and §7 (ordering rules).

In one paragraph: Refactorika is one interface-agnostic core (graph + transforms + checker +
memory) wrapped in two thin front doors — a **standalone Typer CLI** (primary) and an **MCP
server** (secondary). The orchestrator builds a reference-correct symbol graph (Jedi), a planner
turns it into a leaf-to-root worklist of `TransformSpec`s (the LLM adding judgment), deterministic
engines (rope/LibCST/ruff) produce an `EditMap`, and the checker runs the gate stack and commits
or reverts via git. State lives in Redis Iris with a mandatory local-JSON fallback.

The earlier "Claude proposes whole-file `new_content`, the core verifies" model described here
previously is **superseded** — see [v2_spec.md](v2_spec.md) for that historical design.
