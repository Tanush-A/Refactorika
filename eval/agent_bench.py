"""Phase 1 — real agent, harness OFF vs ON.

The fair ablation the whole product rests on: the SAME agent + SAME tasks, with
the only variable being whether its edits route through the verification harness.

  - no-harness arm: agent proposes once -> edit applied raw (no gates) -> graded
    by the independent oracle (repo tests). The first broken edit ships, because
    without the harness there is no signal that anything broke.
  - harness arm: agent proposes -> apply_and_verify (full gate stack, atomic
    commit/rollback). On rollback the failure reason is fed back and the agent
    re-proposes, up to `max_retries`. If still failing, the task is ESCALATED
    (skipped-needs-human) rather than force-committed.

This is what makes "correct refactors landed: no-harness X% -> harness Y%" a real,
oracle-judged number instead of a label. Retries/tokens/wall/escalation come from
the agent loop and fill report sections that were `pending` in Phase 0.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from refactorika.core.apply import apply_and_verify  # noqa: E402
from refactorika.core.storage import Storage  # noqa: E402

from eval.benchmark import OracleGrader  # noqa: E402
from eval.proposers import LocalAgentProposer, Usage  # noqa: E402
from eval.substrates import SubstrateSpec, checkout  # noqa: E402


@dataclass
class AgentTask:
    name: str
    target: str  # path relative to substrate root
    kind: str  # refactor_kind (schema.REFACTOR_KINDS)
    instruction: str


# Tasks the oracle (existing pytest) can actually verify — all touch behavior
# covered by demo_repo/test_orders.py, so a wrong edit fails the tests.
DEMO_TASKS: list[AgentTask] = [
    AgentTask(
        "flatten_compute_total", "orders.py", "flatten_nesting",
        "Reduce the nesting depth of compute_total using early `continue`/guard "
        "clauses. Preserve behavior exactly.",
    ),
    AgentTask(
        "extract_discount_helper", "orders.py", "extract_helper",
        "Extract the per-line tier-discount logic inside compute_total into a new "
        "module-level helper function and call it. Preserve behavior exactly.",
    ),
    AgentTask(
        "dedupe_imports", "orders.py", "reorder_imports",
        "Remove the duplicate `import math` line. Change nothing else.",
    ),
]


def _storage_for_arm() -> Storage:
    # Force the JSON backend in an isolated temp dir (no Redis dependency, no
    # cross-task contamination of the retry counter).
    tmp = Path(mkdtemp(prefix="refactorika-agent-")) / "state.json"
    return Storage(redis_url=None, json_path=tmp)


def _log(msg: str) -> None:
    print(f"    {msg}", flush=True)


def _run_no_harness(task: AgentTask, substrate: SubstrateSpec, proposer, grader) -> dict:
    usage = Usage()
    _log(f"[{task.name}] no-harness: proposing...")
    with checkout(substrate.path) as repo:
        target = repo / task.target
        prop = proposer.propose_edit(task.instruction, task.target, target.read_text())
        usage.add(prop.usage)
        seconds = prop.seconds
        if prop.error:
            _log(f"[{task.name}] no-harness: model error ({prop.error}) after {seconds}s")
            return _record(task, "no_harness", success=False, status="error",
                           retries=0, usage=usage, seconds=seconds, escalated=False)
        if prop.content is None:
            _log(f"[{task.name}] no-harness: unparseable reply after {seconds}s")
            return _record(task, "no_harness", success=False, status="unparseable",
                           retries=0, usage=usage, seconds=seconds, escalated=False)
        target.write_text(prop.content)  # raw apply — no gates
        oracle = grader.grade(repo)
    _log(f"[{task.name}] no-harness: shipped -> oracle "
         f"{'PASS' if oracle else 'FAIL (broken edit shipped)'}  "
         f"({usage.total} tok, {round(seconds,1)}s)")
    return _record(task, "no_harness", success=bool(oracle), status="shipped",
                   retries=0, usage=usage, seconds=seconds, escalated=False,
                   oracle=oracle)


def _run_harness(task: AgentTask, substrate: SubstrateSpec, proposer, grader,
                 max_retries: int) -> dict:
    usage = Usage()
    seconds = 0.0
    storage = _storage_for_arm()
    failure_reason: Optional[str] = None
    with checkout(substrate.path) as repo:
        target = repo / task.target
        attempts = 0
        committed = False
        last_status = "rolled-back"
        for attempt in range(max_retries + 1):
            attempts += 1
            _log(f"[{task.name}] harness: attempt {attempt + 1}/{max_retries + 1} proposing...")
            prop = proposer.propose_edit(
                task.instruction, task.target, target.read_text(), failure_reason
            )
            usage.add(prop.usage)
            seconds += prop.seconds
            if prop.error:
                failure_reason = f"the model call failed ({prop.error}); try again"
                last_status = "error"
                _log(f"[{task.name}] harness: attempt {attempt + 1} model error ({prop.error})")
                continue
            if prop.content is None:
                failure_reason = "your reply contained no parseable Python file body"
                last_status = "unparseable"
                _log(f"[{task.name}] harness: attempt {attempt + 1} unparseable reply")
                continue
            record = apply_and_verify(str(target), prop.content, task.kind, storage)
            last_status = record.status
            if record.status == "committed":
                committed = True
                _log(f"[{task.name}] harness: attempt {attempt + 1} COMMITTED "
                     f"({prop.usage.total} tok, {round(prop.seconds,1)}s)")
                break
            failure_reason = record.failure_reason  # feed the gate's reason back
            _log(f"[{task.name}] harness: attempt {attempt + 1} rolled back "
                 f"({record.failure_reason})")
        oracle = grader.grade(repo) if committed else None
    _log(f"[{task.name}] harness: {'COMMITTED' if committed else 'ESCALATED needs-human'} "
         f"after {attempts} attempt(s) -> oracle "
         f"{'PASS' if oracle else ('FAIL' if committed else 'n/a')}  "
         f"({usage.total} tok, {round(seconds,1)}s)")
    escalated = not committed
    status = "committed" if committed else "skipped-needs-human"
    return _record(task, "harness", success=bool(committed and oracle), status=status,
                   retries=attempts - 1, usage=usage, seconds=seconds,
                   escalated=escalated, oracle=oracle, last_gate_status=last_status,
                   force_committed=False)


def _record(task: AgentTask, arm: str, *, success: bool, status: str, retries: int,
            usage: Usage, seconds: float, escalated: bool,
            oracle: Optional[bool] = None, **extra) -> dict:
    rec = {
        "task": task.name,
        "arm": arm,
        "success": success,
        "status": status,
        "retries": retries,
        "tokens_in": usage.prompt_tokens,
        "tokens_out": usage.completion_tokens,
        "tokens_total": usage.total,
        "seconds": round(seconds, 1),
        "escalated": escalated,
        "oracle_pass": oracle,
    }
    rec.update(extra)
    return rec


def _aggregate_agent(records: list[dict], tasks: list[AgentTask], model: str,
                     price_per_mtok: float) -> dict:
    nh = [r for r in records if r["arm"] == "no_harness"]
    hr = [r for r in records if r["arm"] == "harness"]
    n = len(tasks)

    def rate(rs: list[dict]) -> Optional[float]:
        return round(sum(1 for r in rs if r["success"]) / len(rs), 3) if rs else None

    nh_success = sum(1 for r in nh if r["success"])
    hr_success = sum(1 for r in hr if r["success"])
    broken_shipped_nh = sum(1 for r in nh if not r["success"])
    broken_shipped_hr = sum(1 for r in hr if r["status"] == "committed" and r["oracle_pass"] is False)
    escalated = sum(1 for r in hr if r["escalated"])
    retries_per_success = (
        round(sum(r["retries"] for r in hr if r["success"]) / hr_success, 2)
        if hr_success else None
    )
    tokens_per_task = round(sum(r["tokens_total"] for r in hr) / len(hr)) if hr else 0
    wall_per_task = round(sum(r["seconds"] for r in hr) / len(hr), 1) if hr else 0.0
    total_tokens = sum(r["tokens_total"] for r in records)
    cost = round(total_tokens / 1_000_000 * price_per_mtok, 4)

    return {
        "model": model,
        "tasks": n,
        "headline": {
            "correct_landed_rate": {"no_harness": rate(nh), "harness": rate(hr)},
            "correct_landed": {"no_harness": nh_success, "harness": hr_success},
            "broken_shipped": {"no_harness": broken_shipped_nh, "harness": broken_shipped_hr},
        },
        "autonomy": {
            "status": "measured",
            "autonomous_completion": hr_success,
            "autonomous_completion_rate": rate(hr),
            "escalated_needs_human": escalated,
            "force_committed": 0,
            "retries_per_success": retries_per_success,
            "tokens_per_task": tokens_per_task,
            "wall_seconds_per_task": wall_per_task,
        },
        "cost": {
            "status": "measured",
            "price_per_mtok": price_per_mtok,
            "total_tokens": total_tokens,
            "run_cost_usd": cost,
            "note": "local model — $0" if price_per_mtok == 0 else None,
        },
        "records": records,
    }


def run_agent_benchmark(
    substrate: SubstrateSpec,
    proposer: Optional[LocalAgentProposer] = None,
    max_retries: int = 2,
    task_limit: Optional[int] = None,
    price_per_mtok: float = 0.0,
) -> dict:
    proposer = proposer or LocalAgentProposer()
    grader = OracleGrader()
    tasks = DEMO_TASKS if substrate.name == "demo_repo" else []
    if task_limit is not None:
        tasks = tasks[:task_limit]
    if not tasks:
        raise ValueError(f"no agent tasks defined for substrate {substrate.name}")

    records: list[dict] = []
    for i, task in enumerate(tasks, 1):
        print(f"  task {i}/{len(tasks)}: {task.name} ({task.target})", flush=True)
        for arm_fn, args in (
            (_run_no_harness, (task, substrate, proposer, grader)),
            (_run_harness, (task, substrate, proposer, grader, max_retries)),
        ):
            try:
                records.append(arm_fn(*args))
            except Exception as exc:  # noqa: BLE001 — never let one task kill the run
                arm = "no_harness" if arm_fn is _run_no_harness else "harness"
                _log(f"[{task.name}] {arm}: UNEXPECTED ERROR ({type(exc).__name__}: {exc})")
                records.append(_record(task, arm, success=False, status="error",
                                       retries=0, usage=Usage(), seconds=0.0,
                                       escalated=(arm == "harness")))
    return _aggregate_agent(records, tasks, proposer.id, price_per_mtok)
