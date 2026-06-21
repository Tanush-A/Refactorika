"""Orchestrator: reads confirmed plan, dispatches tasks to specialists in dependency-ordered waves."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core.schema import Plan, PlanTask
from ..core.storage import Storage
from .base import SpecialistAgent
from .complexity_agent import ComplexityAgent
from .dead_code_agent import DeadCodeAgent
from .duplicate_agent import DuplicateAgent
from .import_agent import ImportAgent

_SPECIALISTS: list[SpecialistAgent] = [
    ImportAgent(),
    DeadCodeAgent(),
    ComplexityAgent(),
    DuplicateAgent(),
]


def _route(task: PlanTask) -> SpecialistAgent | None:
    """Return the specialist that handles the task's dominant refactor_kind."""
    if not task.opportunities:
        return None
    dominant = task.opportunities[0].kind
    for s in _SPECIALISTS:
        if dominant in s.supported_kinds:
            return s
    return None


def dispatch_plan(storage: Storage, max_workers: int = 4) -> dict:
    """Read the confirmed plan, dispatch tasks in dependency-ordered waves, return a summary."""
    raw = storage.load_plan()
    if raw is None:
        return {"error": "no plan; call get_plan first"}
    plan = Plan.from_dict(raw)
    if not plan.confirmed:
        return {"error": "plan not confirmed; call confirm_plan first"}

    by_order: dict[int, list[PlanTask]] = defaultdict(list)
    for task in plan.tasks:
        by_order[task.order].append(task)

    committed, rolled_back, skipped = 0, 0, 0
    records: list[dict] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for level in sorted(by_order.keys()):
            wave = by_order[level]
            futures = {}
            for task in wave:
                agent = _route(task)
                if agent is None:
                    skipped += 1
                    continue
                futures[executor.submit(agent.handle, task, storage)] = task

            for future in as_completed(futures):
                try:
                    record = future.result()
                    records.append(record.to_dict())
                    if record.status == "committed":
                        committed += 1
                    elif record.status == "rolled-back":
                        rolled_back += 1
                    else:
                        skipped += 1
                except Exception as exc:  # noqa: BLE001
                    task = futures[future]
                    records.append({"file": task.file, "error": str(exc)})
                    skipped += 1

    return {
        "committed": committed,
        "rolled_back": rolled_back,
        "skipped": skipped,
        "records": records,
    }
