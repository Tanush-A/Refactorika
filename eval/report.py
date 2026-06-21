"""Terminal report renderer + run-over-run diff.

Pure render of the aggregate dict emitted by `eval/benchmark.py`. ASCII only,
alignment-first, no color required (docs/12-benchmark-display-spec.md). The
layout is fixed now; swapping proposer/substrate changes the numbers, never the
sections. Agent-dependent blocks render `pending (...)` rather than fake values.
"""
from __future__ import annotations

from typing import Optional

_WIDTH = 78
_RULE = "-" * _WIDTH


def _pct(x: Optional[float]) -> str:
    return "  -  " if x is None else f"{round(x * 100):>3}%"


def _num(x: Optional[object]) -> str:
    return "-" if x is None else str(x)


# KPIs tracked across runs, with their "good" direction (True = up is good).
TRACKED_KPIS: dict[str, bool] = {
    "full.catch_rate": True,
    "full.broken_landed": False,
    "full.broken_behavior": False,
    "false_rejection_rate": False,
    "committed_unverified": False,
    "opportunities_resolved_pct": True,
    "comprehension_after": False,
    "force_committed": False,
    "calibration_passed": True,
}


def extract_kpis(results: dict) -> dict[str, Optional[float]]:
    rel = results["reliability"]
    full = rel["full"]
    return {
        "full.catch_rate": full["catch_rate"],
        "full.broken_landed": full["broken_landed"],
        "full.broken_behavior": full["broken_by_severity"]["beh"],
        "false_rejection_rate": full["false_rejection_rate"],
        "committed_unverified": full["committed_unverified"],
        "opportunities_resolved_pct": results["enhancement"]["opportunities_resolved_pct"],
        "comprehension_after": results["swag"]["comprehension_proxy"]["avg_after"],
        "force_committed": 0,  # harness never force-commits; tracked to stay 0
        "calibration_passed": results["calibration"]["passed"],
    }


def _meta_lines(meta: dict) -> list[str]:
    tv = meta.get("tool_versions", {})
    return [
        f"REFACTORIKA BENCHMARK · {meta['timestamp']}",
        f"model={meta['model']}   harness=git@{_num(meta['harness_git_sha'])}   "
        f"trials={meta['trials']}/task  seed={meta['seed']}",
        f"substrate={meta['substrate']}   grader={meta['grader']} (independent)   "
        f"py={tv.get('python')}",
    ]


def _headline(h: dict) -> list[str]:
    g, b, s = h["good_landed"], h["broken_shipped"], h["silent_beh_shipped"]
    cost = h["cost"]
    retries = cost["retries_per_task"]
    retry_str = "pending (needs agent)" if retries is None else f"+{retries} retries/task"
    return [
        "HEADLINE  — same proposer, harness OFF (raw) vs ON (full)",
        f"  good refactors landed:          raw {g['raw']}      ->   full {g['full']}",
        f"  broken edits shipped:           raw {b['raw']:<6} ->   full {b['full']}",
        f"  silent behavior breaks shipped: raw {s['raw']:<6} ->   full {s['full']}",
        f"  cost of safety:                 {cost['good_rolled_back']} good edits rolled back,  "
        f"retries {retry_str}",
    ]


def _reliability(rel: dict) -> list[str]:
    lines = [
        "1 · RELIABILITY   (catches bad edits, keeps good ones)",
        "  tier        catch   broken_landed (by severity)        false_rej   unverified",
    ]
    for tier in ("raw", "lint_type", "full"):
        r = rel[tier]
        sev = r["broken_by_severity"]
        sev_str = (
            f"{r['broken_landed']:<3} "
            f"syn {sev['syn']} · lint {sev['lint']} · type {sev['type']} · beh {sev['beh']}"
        )
        lines.append(
            f"  {tier:<11}{_pct(r['catch_rate'])}   {sev_str:<33}"
            f"{_pct(r['false_rejection_rate'])}      {r['committed_unverified']}"
        )
    beh_lt = rel["lint_type"]["broken_by_severity"]["beh"]
    beh_full = rel["full"]["broken_by_severity"]["beh"]
    lines.append(
        f"  > lint_type->full delta = {beh_lt - beh_full} silent behavior break(s) "
        "caught that nothing else saw"
    )
    return lines


def _enhancement(e: dict) -> list[str]:
    return [
        "2 · ENHANCEMENT   (landed refactors actually improved the code)",
        f"  opportunities resolved:   {e['opportunities_before']} -> {e['opportunities_after']}   "
        f"({e['opportunities_resolved_pct']}% reduction)",
        f"  max nesting depth:        {e['max_nesting_before']} -> {e['max_nesting_after']}      "
        f"longest function: {e['longest_fn_before']} -> {e['longest_fn_after']} lines",
        f"  files improved:           {e['files_improved']} / {e['files_touched']} touched",
    ]


def _agent_headline(agent: dict) -> list[str]:
    h = agent["headline"]
    r = h["correct_landed_rate"]
    c = h["correct_landed"]
    b = h["broken_shipped"]
    n = agent["tasks"]
    return [
        f"AGENT HEADLINE  — same agent ({agent['model']}), harness OFF vs ON   "
        f"({n} task{'s' if n != 1 else ''})",
        f"  correct refactors landed:   no-harness {_pct(r['no_harness'])} "
        f"({c['no_harness']}/{n})   ->   harness {_pct(r['harness'])} ({c['harness']}/{n})",
        f"  broken refactors shipped:   no-harness {b['no_harness']}        ->   "
        f"harness {b['harness']}",
    ]


def _autonomy(results: dict) -> list[str]:
    agent = results.get("agent")
    if not agent or agent["autonomy"].get("status") != "measured":
        reason = results["autonomy"].get("reason", "needs agent loop")
        return ["3 · AUTONOMY & COST   (completed without a human)", f"  pending ({reason})"]
    a = agent["autonomy"]
    return [
        "3 · AUTONOMY & COST   (completed without a human)",
        f"  autonomous completion:    {a['autonomous_completion']}/{agent['tasks']}  "
        f"({_pct(a['autonomous_completion_rate'])})",
        f"  escalated needs-human:    {a['escalated_needs_human']}        "
        f"force-committed: {a['force_committed']}",
        f"  retries per success:      {_num(a['retries_per_success'])}      "
        f"tokens/task: {a['tokens_per_task']}     wall: {a['wall_seconds_per_task']}s/task",
    ]


def _calibration(c: dict) -> list[str]:
    status = "OK" if c["passed"] == c["total"] else "FAILED"
    failed = [x["name"] for x in c["controls"] if not x["passed"]]
    line = f"CALIBRATION   controls {c['passed']}/{c['total']} passed   (harness self-check {status})"
    if failed:
        line += f"  -> FAILED: {', '.join(failed)}"
    return [line]


def _cost_line(results: dict) -> str:
    agent = results.get("agent")
    if not agent or agent["cost"].get("status") != "measured":
        s = results["swag"]["cost_dollars"]
        return f"4b · COST:           {s['status']} ({s['reason']})"
    c = agent["cost"]
    note = c.get("note") or f"${c['run_cost_usd']} @ ${c['price_per_mtok']}/Mtok"
    return f"4b · COST:           {c['total_tokens']} tokens this run   ({note})"


def _swag(swag: dict, results: dict) -> list[str]:
    ch = swag["code_health"]
    cp = swag["comprehension_proxy"]
    cx_tool = ch["complexity_tool"]

    def cx(v: Optional[object]) -> str:
        return _num(v) if cx_tool else "n/a"

    lines = [
        "4c · CODE HEALTH (before -> after, what landed)",
        f"  LOC:               {ch['loc_before']} -> {ch['loc_after']}        "
        f"max nesting:   {ch['max_nesting_before']} -> {ch['max_nesting_after']}",
        f"  avg complexity:    {cx(ch['avg_complexity_before'])} -> {cx(ch['avg_complexity_after'])}"
        f"          longest fn:    {ch['longest_fn_before']} -> {ch['longest_fn_after']} lines"
        + ("" if cx_tool else "   (install radon for complexity)"),
        f"  context files:     {ch['context_files_before']} -> {ch['context_files_after']}",
        "",
        "4a · DOWNSTREAM AGENT ROI",
        f"  context-size proxy (avg tokens/module):  {cp['avg_before']} -> {cp['avg_after']}   "
        "[proxy — direction only]",
        f"  real-agent ROI:    {swag['downstream_roi_real']['status']} "
        f"({swag['downstream_roi_real']['reason']})",
        "",
        _cost_line(results),
    ]
    lines += _refactorbench_lines(results)
    return lines


def _refactorbench_lines(results: dict) -> list[str]:
    rb = results.get("refactorbench")
    if not rb or rb.get("status") != "measured":
        s = results["swag"]["refactorbench"]
        return [f"4d · REFACTORBENCH:  {s['status']} ({s['reason']})"]
    sr = rb["solve_rate"]
    bs = rb["broken_shipped"]
    a = rb["anchors"]
    return [
        f"4d · REFACTORBENCH ({rb['repo']}, {rb['tasks']} task{'s' if rb['tasks'] != 1 else ''}, "
        f"{rb['model']}) — real unseen OSS repos",
        f"     gates: {rb['gates_used']}",
        f"     oracle: {rb['oracle']}",
        f"     solve rate:         no-harness {_pct(sr['no_harness'])}   ->   "
        f"harness {_pct(sr['harness'])}    (anchors: LM {_pct(a['lm_agent'])} / "
        f"human {_pct(a['human'])})",
        f"     broken edits shipped: no-harness {bs['no_harness']}        ->   "
        f"harness {bs['harness']}        escalated: {rb['escalated']}",
    ]


def _diff_block(results: dict, previous: Optional[dict]) -> list[str]:
    if not previous:
        return ["Δ vs previous  (no previous run on file — this is the baseline)"]
    prev_meta = previous.get("meta", {})
    cur = extract_kpis(results)
    prv = extract_kpis(previous)
    lines = [
        f"Δ vs previous run  (harness git@{_num(prev_meta.get('harness_git_sha'))} · "
        f"{_num(prev_meta.get('timestamp'))})"
    ]
    for kpi, up_good in TRACKED_KPIS.items():
        c, p = cur.get(kpi), prv.get(kpi)
        if c is None or p is None:
            continue
        delta = c - p
        if delta == 0:
            marker, verdict = "=", "unchanged"
        else:
            improved = (delta > 0) == up_good
            marker = "^" if delta > 0 else "v"
            verdict = "IMPROVED" if improved else "REGRESSED"
        dstr = f"{marker}{abs(round(delta, 3))}" if delta else "="
        lines.append(f"  {kpi:<28}{_num(round(c, 3)):>8}   {dstr:<7} {verdict}")
    return lines


def render(results: dict, previous: Optional[dict] = None) -> str:
    out: list[str] = []
    out += _meta_lines(results["meta"])
    out.append(_RULE)
    out.append("")
    out += _headline(results["headline"])
    if results.get("agent"):
        out.append("")
        out += _agent_headline(results["agent"])
    out.append("")
    out += _reliability(results["reliability"])
    out.append("")
    out += _enhancement(results["enhancement"])
    out.append("")
    out += _autonomy(results)
    out.append("")
    out += _calibration(results["calibration"])
    out.append("")
    out += _swag(results["swag"], results)
    out.append("")
    out.append(_RULE)
    out += _diff_block(results, previous)
    return "\n".join(out)
