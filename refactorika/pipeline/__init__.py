"""The agentic pipeline: Scout (parallel read) -> Planner (single) -> Refactor
(sequential, leaf-to-root) -> Checker (deterministic gate). The orchestrator wires
them into a plain loop. Reads parallelize; writes stay single-threaded.
"""
