# Four-Arm Agent Benchmark Contract

This contract is frozen before the agent runtime is refactored. Diagnostic
ablations may be added, but they are not primary arms and must not be pooled
with these results.

| Arm | Agent loop | Refactorika harness |
|---|---:|---:|
| `off` | No | No |
| `on` | No | Yes |
| `agentic` | Yes | No |
| `agentic+harness` | Yes | Yes |

## Controlled variables

Within each `(case, trial)`, all arms use the same model, temperature, baseline
repository, initial user prompt, and hidden grader. Every repository copy is
isolated. Hidden tests and structural grading expectations are unavailable to
all agents and to Refactorika verification.

The two loop arms use one shared loop implementation and the same developer
exploration, repository-reading, diagnostic, and patch-submission schemas. The
harness intervention adds repository audit/planning context, memory/context
retrieval when available, atomic verification and rollback, structured repair
feedback, campaign tracking, and completion auditing.

The non-loop arms remain useful because they separate loop value from harness
value. Optional analysis-only and verification-only configurations are
diagnostic ablations, not additional primary arms.

## Outcome and failure policy

Correctness is graded only after execution by held-out behavior and structural
oracles. Infrastructure failures invalidate the affected benchmark run rather
than count as model failures. Every loop terminates with one of the reasons in
`eval/agents/schema.py`.

Acceptance targets are reporting goals, not permission to tune fixtures,
hidden tests, or grading toward a preferred arm. Parallel execution is an
operational optimization; sequential execution remains the timing reference.
