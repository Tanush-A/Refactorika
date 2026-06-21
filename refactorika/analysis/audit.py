"""Repo-wide audit + dependency-ordered planning (advisory, read-only).

Reuses the existing per-file analysis (`analyze_file`) and call graph
(`CallGraph`) — no new analysis logic. The audit aggregates opportunities into a
ranked report; the plan orders deviating files fewest-dependents-first so
low-blast-radius edits land before the modules many things depend on.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from refactorika.analysis.call_graph import CallGraph, _collect_py_files, _module_name
from refactorika.core.analyze import analyze_file
from refactorika.core.schema import AuditEntry, Plan, PlanTask, RepoAudit
from refactorika.core.storage import Storage


def audit_repo(path: str, storage: Storage) -> RepoAudit:
    """Walk the repo, aggregate per-file opportunities into a ranked report."""
    files, _root = _collect_py_files(path)
    entries: list[AuditEntry] = []
    by_kind: Counter = Counter()
    rank_by_kind: defaultdict[str, int] = defaultdict(int)
    total = 0

    for f in files:
        opps = analyze_file(str(f), storage).opportunities
        if not opps:
            continue  # a "deviating" file is one with >= 1 opportunity
        total += len(opps)
        for o in opps:
            by_kind[o.kind] += 1
            rank_by_kind[o.kind] += o.rank
        entries.append(
            AuditEntry(file=str(f), opportunities=opps, score=sum(o.rank for o in opps))
        )

    entries.sort(key=lambda e: e.score, reverse=True)

    dominant = None
    if rank_by_kind:  # the kind worth doing most across the repo (highest summed rank)
        top_kind = max(rank_by_kind, key=lambda k: rank_by_kind[k])
        dominant = f"{top_kind} ({by_kind[top_kind]} sites)"

    return RepoAudit(
        repo=path,
        files_scanned=len(files),
        total_opportunities=total,
        by_kind=dict(by_kind),
        dominant_finding=dominant,
        entries=entries,
    )


def build_plan(path: str, storage: Storage) -> Plan:
    """Order deviating files fewest-dependents-first; persist + return the plan."""
    audit = audit_repo(path, storage)
    files, root = _collect_py_files(path)
    cg = CallGraph.build(path)
    module_of = {str(f): _module_name(f, root) for f in files}

    tasks: list[PlanTask] = []
    for entry in audit.entries:  # only deviating files (>=1 opportunity)
        module = module_of.get(entry.file, "")
        deps = cg.dependents_of(module) if module else []
        tasks.append(
            PlanTask(
                file=entry.file,
                opportunities=entry.opportunities,
                dependents=deps,
                order=0,
            )
        )

    # Fewest-dependents-first (low blast radius first); tie-break by score desc.
    def _order_key(t: PlanTask) -> tuple[int, int]:
        return (len(t.dependents), -sum(o.rank for o in t.opportunities))

    tasks.sort(key=_order_key)
    for i, t in enumerate(tasks):
        t.order = i

    plan = Plan(repo=path, dominant_finding=audit.dominant_finding, tasks=tasks)
    storage.save_plan(plan.to_dict())
    return plan
