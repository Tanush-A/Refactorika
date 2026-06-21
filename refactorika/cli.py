"""Standalone CLI — point it at a repo and watch it refactor, verified.

``refactorika <dir>`` runs the full pipeline on a throwaway copy (dry-run) and prints
the leaf-to-root plan, each verified edit, and a before/after metrics table. ``--apply``
runs in place and commits. No agent required; Redis is optional (falls back to files).
"""

from __future__ import annotations

from typing import Optional

import typer

from refactorika.core.storage import Storage
from refactorika.metrics import metrics_delta

_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"


def _tri(value: Optional[bool]) -> str:
    if value is True:
        return _c("pass", _GREEN)
    if value is False:
        return _c("FAIL", _RED)
    return _c("skip", _YELLOW)


def _entry(
    path: str = typer.Argument(..., help="Path to the Python repo/dir to refactor."),
    apply: bool = typer.Option(False, "--apply", help="Write changes in place and commit."),
    show_graph: bool = typer.Option(False, "--show-graph", help="Print the symbol graph and exit."),
    show_plan: bool = typer.Option(False, "--show-plan", help="Print the plan and exit."),
    no_tests: bool = typer.Option(False, "--no-tests", help="Skip the test gates (faster)."),
    use_llm: bool = typer.Option(False, "--llm", help="Use the LLM planner (needs API key)."),
    rename: list[str] = typer.Option(
        None, "--rename",
        help="Reference-correct rename, repeatable: 'module.qualname=new_name'."),
) -> None:
    """Refactor a Python repo with verified, graph-driven transforms."""
    renames = _parse_renames(rename)
    if show_graph:
        _print_graph(path)
        return
    if show_plan:
        _print_plan(path, use_llm, renames)
        return
    _run(path, apply=apply, run_tests=not no_tests, use_llm=use_llm, renames=renames)


def _parse_renames(rename: Optional[list[str]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for r in rename or []:
        if "=" in r:
            qual, new = r.split("=", 1)
            pairs.append((qual.strip(), new.strip()))
    return pairs


# --------------------------------------------------------------------------- actions
def _run(path: str, *, apply: bool, run_tests: bool, use_llm: bool,
         renames: Optional[list[tuple[str, str]]] = None) -> None:
    from refactorika.pipeline.orchestrator import run_pipeline

    planner = _build_planner(use_llm, renames)
    storage = Storage()
    mode = _c("APPLY (in place)", _RED) if apply else _c("dry-run (copy)", _DIM)
    typer.echo(f"\n{_BOLD}Refactorika{_RESET}  ·  {path}  ·  {mode}  ·  storage={storage.backend}")

    res = run_pipeline(path, apply=apply, planner=planner, storage=storage, run_tests=run_tests)

    typer.echo(f"\n  baseline suite: {_tri(res.baseline_tests)}  "
               f"{_DIM}{res.baseline_detail}{_RESET}")
    if res.cycles:
        typer.echo(f"  {_c('cycles', _YELLOW)}: {res.cycles}")

    committed = [r for r in res.records if r["status"] == "committed"]
    reverted = [r for r in res.records if r["status"] == "rolled-back"]
    typer.echo(f"\n{_BOLD}Edits{_RESET} — {len(committed)} committed, {len(reverted)} reverted")
    for r in res.records:
        _print_record(r)

    typer.echo(f"\n{_BOLD}Metrics{_RESET}")
    delta = metrics_delta(res.metrics_before, res.metrics_after)
    for k in res.metrics_before:
        b, a, d = res.metrics_before[k], res.metrics_after[k], round(delta[k], 2)
        arrow = "" if d == 0 else _c(f"  ({d:+g})", _GREEN if _is_improvement(k, d) else _DIM)
        typer.echo(f"  {k:16} {b:>7} -> {a:>7}{arrow}")

    typer.echo(f"\n  finale suite:  {_tri(res.finale_tests)}  {_DIM}{res.finale_detail}{_RESET}")
    if not apply:
        typer.echo(f"\n{_DIM}dry-run — working copy at {res.path};"
                   f" re-run with --apply to commit.{_RESET}")
    typer.echo("")


def _print_record(r: dict) -> None:
    status = r["status"]
    color = _GREEN if status == "committed" else _RED if status == "rolled-back" else _YELLOW
    checks = " ".join(
        f"{k}:{_tri(v)}" for k, v in r["checks"].items() if v is not None
    )
    files = ", ".join(r["files"])
    typer.echo(f"  {_c(status, color):>22}  {r['refactor_kind']:18} {files}")
    if checks:
        typer.echo(f"      {_DIM}{checks}{_RESET}")
    if r.get("failure_reason"):
        typer.echo(f"      {_c(r['failure_reason'], _RED)}")


def _print_graph(path: str) -> None:
    from refactorika.graph.order import reachable_from, topo_order
    from refactorika.graph.resolver import build_graph

    g = build_graph(path)
    order, cycles = topo_order(g)
    reach = reachable_from(g, g.entry_points)
    typer.echo(f"\n{_BOLD}Symbol graph{_RESET} — {len(g.symbols)} nodes")
    for q in order:
        s = g.symbols[q]
        if s.kind == "module":
            continue
        tags = []
        if q in g.entry_points:
            tags.append(_c("entry", _GREEN))
        if q not in reach:
            tags.append(_c("DEAD", _RED))
        deps = sorted(d.split(".")[-1] for d in g.outgoing(q))
        tagstr = (" " + " ".join(tags)) if tags else ""
        depstr = f"  {_DIM}-> {', '.join(deps)}{_RESET}" if deps else ""
        typer.echo(f"  {s.kind:8} {q}{tagstr}{depstr}")
    if cycles:
        typer.echo(f"\n  {_c('cycles', _YELLOW)}: {cycles}")
    typer.echo("")


def _print_plan(path: str, use_llm: bool, renames=None) -> None:
    from refactorika.graph.resolver import build_graph
    from refactorika.pipeline.planner import deterministic_plan

    g = build_graph(path)
    planner = _build_planner(use_llm, renames) or deterministic_plan
    plan = planner(g, root=path)
    typer.echo(f"\n{_BOLD}Plan{_RESET} (leaf-to-root) — {len(plan.items)} items")
    for it in plan.items:
        s = it.spec
        typer.echo(f"  [{it.order_index:>3}] {s.kind:18} {s.target}")
        if s.rationale:
            typer.echo(f"        {_DIM}{s.rationale}{_RESET}")
    typer.echo("")


def _is_improvement(metric: str, delta: int) -> bool:
    # Lower is better for these metrics.
    if metric in ("sloc", "lloc", "dead_symbols", "total_complexity", "max_complexity"):
        return delta < 0
    return False


def _build_planner(use_llm: bool, renames):
    """Compose the planner: optional LLM judgment as the base, optional renames first."""
    base = _llm_planner() if use_llm else None
    if renames:
        from refactorika.pipeline.planner import deterministic_plan, renames_first_planner

        return renames_first_planner(renames, base=base or deterministic_plan)
    return base


def _llm_planner():
    try:
        from refactorika.pipeline.planner_llm import make_llm_planner

        return make_llm_planner()
    except Exception:
        typer.echo(_c("  --llm requested but LLM planner unavailable; using deterministic plan.",
                      _YELLOW))
        return None


def main() -> None:
    typer.run(_entry)


if __name__ == "__main__":
    main()
