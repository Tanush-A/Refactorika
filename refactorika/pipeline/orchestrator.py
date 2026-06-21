"""The orchestrator — the plain loop that runs a refactor end to end.

Builds the program graph, plans a leaf-to-root worklist, then walks it one item at a
time: dispatch to the deterministic engine -> hand the EditMap to the Checker -> commit
or revert. After the worklist it cascades dead-code removal to a fixpoint (a removal can
orphan a helper, then a constant). Dry-run operates on a throwaway copy so nothing is
mutated until ``--apply``.

Writes are single-threaded by construction (one item at a time); the graph is rebuilt
before each item so positions/qualnames stay correct after a prior edit.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from refactorika.core.schema import PipelineResult, TransformSpec, Worklist
from refactorika.graph.order import impact_of, reachable_from
from refactorika.graph.resolver import build_graph
from refactorika.metrics import repo_metrics
from refactorika.pipeline.checker import Checker, impacted_test_node_ids
from refactorika.pipeline.planner import deterministic_plan
from refactorika.transforms.base import dispatch

# A planner is any function graph -> Worklist (deterministic_plan or an LLM planner).
Planner = Callable[..., Worklist]

_SKIP_COPY = {".git", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def run_pipeline(
    root: str,
    *,
    apply: bool = False,
    planner: Optional[Planner] = None,
    storage=None,
    run_tests: bool = True,
    cascade: bool = True,
) -> PipelineResult:
    """Run the full pipeline. Returns a PipelineResult with records + before/after metrics."""
    workdir = str(Path(root).resolve()) if apply else _copy_to_temp(root)
    _ensure_git(workdir)

    before = repo_metrics(workdir)
    graph = build_graph(workdir)
    checker = Checker(workdir, storage=storage, run_tests=run_tests)

    plan = (planner or deterministic_plan)(graph)
    records: list[dict] = []

    for item in plan.items:
        graph = build_graph(workdir)  # correctness: positions/qualnames may have shifted
        spec = item.spec
        if spec.kind != "cleanup" and spec.target not in graph.symbols:
            continue  # already removed by a cascade or a prior item
        try:
            edits = dispatch(spec, workdir, graph)
        except Exception:
            continue
        if not edits:
            continue
        node_ids = impacted_test_node_ids(graph, workdir, item.impact)
        rec = checker.verify_apply(edits, spec.kind, test_node_ids=node_ids)
        records.append(rec.to_dict())

    if cascade:
        records.extend(_cascade_dead_code(workdir, checker))

    after = repo_metrics(workdir)
    return PipelineResult(
        path=workdir,
        records=records,
        metrics_before=before,
        metrics_after=after,
        cycles=plan.cycles,
        applied=apply,
    )


def _cascade_dead_code(workdir: str, checker: Checker, max_rounds: int = 10) -> list[dict]:
    """Remove newly-orphaned dead symbols until a fixpoint (or max_rounds)."""
    out: list[dict] = []
    seen: set[str] = set()
    for _ in range(max_rounds):
        graph = build_graph(workdir)
        reach = reachable_from(graph, graph.entry_points)
        dead = [
            q for q in graph.symbols
            if q not in reach and graph.symbols[q].kind != "module"
            and graph.symbols[q].is_private and q not in seen
        ]
        if not dead:
            break
        progressed = False
        for q in dead:
            seen.add(q)
            spec = TransformSpec(kind="remove_dead_code", target=q,
                                 rationale="orphaned by a prior removal (cascade)")
            try:
                edits = dispatch(spec, workdir, build_graph(workdir))
            except Exception:
                continue
            if not edits:
                continue
            node_ids = impacted_test_node_ids(graph, workdir, sorted(impact_of(graph, q)))
            rec = checker.verify_apply(edits, "remove_dead_code", test_node_ids=node_ids)
            out.append(rec.to_dict())
            progressed = True
        if not progressed:
            break
    return out


# --------------------------------------------------------------------------- helpers
def _copy_to_temp(root: str) -> str:
    src = Path(root).resolve()
    dst = Path(tempfile.mkdtemp(prefix="refactorika_"))
    target = dst / src.name
    shutil.copytree(src, target, ignore=shutil.ignore_patterns(*_SKIP_COPY))
    return str(target)


def _ensure_git(workdir: str) -> None:
    """Make *workdir* a git repo with a clean baseline commit so per-edit commits work."""
    wd = Path(workdir)
    if (wd / ".git").exists():
        return
    env = {"GIT_AUTHOR_NAME": "refactorika", "GIT_AUTHOR_EMAIL": "bot@refactorika",
           "GIT_COMMITTER_NAME": "refactorika", "GIT_COMMITTER_EMAIL": "bot@refactorika"}
    import os

    run_env = {**os.environ, **env}
    subprocess.run(["git", "-C", workdir, "init", "-q"], capture_output=True, env=run_env)
    # Persist identity locally so the checker's per-edit commits work without env.
    subprocess.run(["git", "-C", workdir, "config", "user.name", "refactorika"],
                   capture_output=True)
    subprocess.run(["git", "-C", workdir, "config", "user.email", "bot@refactorika"],
                   capture_output=True)
    subprocess.run(["git", "-C", workdir, "add", "-A"], capture_output=True, env=run_env)
    subprocess.run(["git", "-C", workdir, "commit", "-q", "-m", "baseline"],
                   capture_output=True, env=run_env)
