"""Render the edit log — visible verification is the whole product.

Run with ``python -m refactorika.dashboard``.
"""

from __future__ import annotations

from .core.storage import Storage
from .observability import capture_exception, init_sentry

_MARK = {True: "PASS", False: "FAIL", None: "skip"}
_STATUS = {
    "committed": "COMMITTED ✓",
    "rolled-back": "ROLLED BACK ✗",
    "skipped-needs-human": "NEEDS HUMAN ⚠",
}


def render(log: list[dict]) -> str:
    lines = ["", "  Refactorika — edit log", "  " + "=" * 56]
    for i, r in enumerate(log, 1):
        c = r["checks"]
        gates = "  ".join(f"{g}:{_MARK[c[g]]}" for g in ("parse", "lint", "typecheck", "tests"))
        lines.append("")
        lines.append(f"  #{i}  {r['refactor_kind']}  on  {r['file'].split('/')[-1]}")
        lines.append(f"      gates: {gates}")
        lines.append(
            f"      status: {_STATUS.get(r['status'], r['status'])}  (retries: {r['retries']})"
        )
        if r["failure_reason"]:
            lines.append(f"      reason: {r['failure_reason']}")
    lines.append("")
    return "\n".join(lines)


def _base(path: str) -> str:
    return path.split("/")[-1] if path else path


def render_audit(audit: dict) -> str:
    repo = audit.get("repo", "?")
    files_scanned = audit.get("files_scanned", 0)
    total = audit.get("total_opportunities", 0)
    by_kind = audit.get("by_kind") or {}
    dominant = audit.get("dominant_finding")
    entries = audit.get("entries") or []

    lines = ["", "  Refactorika — repo audit", "  " + "=" * 56]
    lines.append(f"  repo: {repo}")
    lines.append(f"  files scanned: {files_scanned}    opportunities: {total}")
    lines.append("")
    lines.append(f"  headline: {dominant if dominant else 'nothing dominant'}")

    if by_kind:
        lines.append("")
        lines.append("  by kind:")
        for kind in sorted(by_kind):
            lines.append(f"      {kind:<28}  {by_kind[kind]}")

    lines.append("")
    if not entries:
        lines.append("  no opportunities found")
        lines.append("")
        return "\n".join(lines)

    ranked = sorted(entries, key=lambda e: e.get("score", 0), reverse=True)
    lines.append(f"  {'file':<28}  {'score':>6}  {'#opps':>6}")
    lines.append("  " + "-" * 46)
    for e in ranked:
        name = _base(e.get("file", "?"))
        score = e.get("score", 0)
        nopps = len(e.get("opportunities") or [])
        lines.append(f"  {name:<28}  {score:>6}  {nopps:>6}")
    lines.append("")
    return "\n".join(lines)


def render_plan(plan: dict) -> str:
    repo = plan.get("repo", "?")
    dominant = plan.get("dominant_finding")
    tasks = plan.get("tasks") or []
    confirmed = plan.get("confirmed", False)
    decision = plan.get("decision")

    if confirmed:
        banner = "CONFIRMED ✓"
    else:
        banner = "UNCONFIRMED"
    if decision:
        banner = f"{banner}  ({decision})"

    lines = ["", "  Refactorika — refactor plan", "  " + "=" * 56]
    lines.append(f"  repo: {repo}")
    lines.append(f"  status: {banner}")
    lines.append(f"  headline: {dominant if dominant else 'nothing dominant'}")
    lines.append("  order: fewest-dependents-first")
    lines.append("")

    if not tasks:
        lines.append("  no tasks planned")
        lines.append("")
        return "\n".join(lines)

    ordered = sorted(tasks, key=lambda t: t.get("order", 0))
    for t in ordered:
        order = t.get("order", 0)
        name = _base(t.get("file", "?"))
        nopps = len(t.get("opportunities") or [])
        ndeps = len(t.get("dependents") or [])
        lines.append(f"  #{order:<3} {name:<28}  {nopps} opps  {ndeps} dependents")
    lines.append("")
    return "\n".join(lines)


def render_campaign(audit_before: dict, plan: dict, log: list[dict], audit_after: dict) -> str:
    audit_before = audit_before or {}
    plan = plan or {}
    log = log or []
    audit_after = audit_after or {}

    before_scores = {e.get("file"): e.get("score", 0) for e in (audit_before.get("entries") or [])}
    after_scores = {e.get("file"): e.get("score", 0) for e in (audit_after.get("entries") or [])}

    improved = 0
    for file, before in before_scores.items():
        after = after_scores.get(file, 0)
        if after < before:
            improved += 1
    total_files = len(before_scores)

    x = audit_before.get("total_opportunities", 0) or 0
    y = audit_after.get("total_opportunities", 0) or 0
    if x > 0:
        pct = round((x - y) / x * 100)
    else:
        pct = 0

    health = (
        f"  HEALTH: opportunities {x} → {y} (−{pct}%) · files improved {improved}/{total_files}"
    )

    sections = [
        render_audit(audit_before),
        render_plan(plan),
        "",
        "  EXECUTION",
        render(log),
        "  " + "=" * 56,
        health,
        "",
    ]
    return "\n".join(sections)


def main() -> None:
    init_sentry("dashboard")
    try:
        print(render(Storage().get_log()))
    except Exception as exc:
        capture_exception(exc, component="dashboard", phase="render")
        raise


if __name__ == "__main__":
    main()
