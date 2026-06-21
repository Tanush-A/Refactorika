"""Dispatch confirmed-plan tasks to specialists, through the verified deterministic engine.

Each specialist brings judgment; the deterministic transforms + the shared Checker bring
correctness (impact-scoped gates, commit or revert). Writes are **serialized**: a committed
edit shifts symbol positions/qualnames, so the graph is rebuilt before each task and only one
task mutates the tree at a time (the engine cannot be parallel-safe across writes). Agents whose
kind has no deterministic engine still fall back to their legacy text path inside ``handle``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..core.schema import Plan, PlanTask
from ..core.storage import Storage
from ..graph.resolver import build_graph
from ..pipeline.checker import Checker
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


def _route(
    task: PlanTask, specialists: Optional[list[SpecialistAgent]] = None
) -> SpecialistAgent | None:
    """Return the specialist that handles the task's dominant refactor_kind."""
    if not task.opportunities:
        return None
    dominant = task.opportunities[0].kind
    for s in specialists or _SPECIALISTS:
        if dominant in s.supported_kinds:
            return s
    return None


def dispatch_plan(
    storage: Storage,
    *,
    specialists: Optional[list[SpecialistAgent]] = None,
    run_tests: bool = True,
) -> dict:
    """Read the confirmed plan and dispatch its tasks (dependency order) through the engine.

    Returns a summary {committed, rolled_back, skipped, records}. Writes are serialized; the
    graph is rebuilt before each task so every agent sees the current tree.
    """
    raw = storage.load_plan()
    if raw is None:
        return {"error": "no plan; build a plan first"}
    plan = Plan.from_dict(raw)
    if not plan.confirmed:
        return {"error": "plan not confirmed; confirm_plan first"}

    root = plan.repo
    checker = Checker(root, storage=storage, run_tests=run_tests)

    by_order: dict[int, list[PlanTask]] = defaultdict(list)
    for task in plan.tasks:
        by_order[task.order].append(task)

    committed, rolled_back, skipped = 0, 0, 0
    records: list[dict] = []

    for level in sorted(by_order.keys()):
        for task in by_order[level]:
            agent = _route(task, specialists)
            if agent is None:
                skipped += 1
                continue
            graph = build_graph(root)  # rebuild: prior commits shift positions/qualnames
            try:
                record = agent.handle(task, storage, graph=graph, checker=checker)
            except Exception as exc:  # noqa: BLE001 — one bad task must not sink the campaign
                records.append({"file": task.file, "error": str(exc)})
                skipped += 1
                continue
            records.append(record.to_dict())
            if record.status == "committed":
                committed += 1
            elif record.status == "rolled-back":
                rolled_back += 1
            else:
                skipped += 1

    return {
        "committed": committed,
        "rolled_back": rolled_back,
        "skipped": skipped,
        "records": records,
    }


def run_campaign(
    path: str,
    storage: Optional[Storage] = None,
    *,
    run_tests: bool = True,
    specialists: Optional[list[SpecialistAgent]] = None,
) -> dict:
    """One-shot agentic campaign: audit -> dependency-ordered plan -> dispatch via the engine.

    The "run like main" entry: builds and auto-confirms a plan for *path*, then dispatches its
    tasks to the specialists, applying every verified edit in place (each gated + committed, any
    failure reverted). Returns the dispatch summary plus the plan's headline finding.
    """
    from ..analysis.audit import build_plan

    storage = storage or Storage()
    plan = build_plan(path, storage)
    plan.confirmed = True
    storage.save_plan(plan.to_dict())
    summary = dispatch_plan(storage, specialists=specialists, run_tests=run_tests)
    summary["dominant_finding"] = plan.dominant_finding
    summary["tasks"] = len(plan.tasks)
    return summary
