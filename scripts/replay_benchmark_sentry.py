"""
Replay real benchmark result files into Sentry, preserving original timestamps.

This tells the true story:
  10:22 - sonnet-4.5-pilot       ON clr=0.5  false_rej=0.5  (first warning)
  10:25 - sonnet-4.5-full        ON clr=0.1  false_rej=0.9  (disaster → pivot decision)
  11:02 - full-system-pilot      ON clr=1.0  regressions=0  (pivot works)
  11:06 - full-system-9x1        ON clr=1.0  regressions=0  (9-case suite passes)
  11:18 - full-system-9x3        ON clr=1.0  regressions=0  (3-trial confirmation)
  11:48 - one-call-v2-pilot      ON clr=0.0                 (alternative explored, dropped)
  12:20 - quick-agent-smoke      ON clr=0.0                 (agent arm baseline)
  12:27 - quick-agent-smoke2     ON clr=1.0                 (agent arm fixed)

Run:
    source .venv/bin/activate
    SENTRY_DSN=$(grep SENTRY_DSN .env | cut -d= -f2-) python scripts/replay_benchmark_sentry.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import sentry_sdk
from sentry_sdk import capture_event

from refactorika.observability import scrub_event

DSN = os.environ.get("SENTRY_DSN", "").strip()
if not DSN:
    sys.exit("SENTRY_DSN not set")

sentry_sdk.init(
    dsn=DSN,
    environment="development",
    send_default_pii=False,
    include_local_variables=False,
    traces_sample_rate=0.0,
    before_send=scrub_event,
)

RESULTS = Path("eval/results")

# Ordered chronologically — this is the actual sequence of runs
RUNS = [
    ("sonnet-4.5-pilot.json",              "harness",      "harness-pilot"),
    ("sonnet-4.5-full.json",               "harness",      "harness-full-disaster"),
    ("sonnet-4.5-full-system-pilot.json",  "full-system",  "full-system-pivot-pilot"),
    ("sonnet-4.5-full-system-9x1.json",    "full-system",  "full-system-9cases-1trial"),
    ("sonnet-4.5-full-system-9x3.json",    "full-system",  "full-system-9cases-3trial"),
    ("sonnet-4.5-one-call-v2-pilot.json",  "full-system",  "one-call-v2-explored"),
    ("quick-agent-smoke.json",             "full-system",  "agent-smoke-v1"),
    ("quick-agent-smoke2.json",            "full-system",  "agent-smoke-v2"),
]


def load(filename: str) -> dict:
    with open(RESULTS / filename) as f:
        return json.load(f)


def send_event(fingerprint: str, level: str, message: str,
               timestamp: str, tags: dict) -> None:
    event = {
        "event_id": uuid.uuid4().hex,
        "timestamp": timestamp,
        "platform": "python",
        "level": level,
        "fingerprint": [fingerprint],
        "message": message,
        "tags": {k: str(v)[:200] for k, v in tags.items()},
    }
    capture_event(event)
    time.sleep(0.05)


def send_exception(fingerprint: str, exc_type: str, message: str,
                   timestamp: str, tags: dict) -> None:
    event = {
        "event_id": uuid.uuid4().hex,
        "timestamp": timestamp,
        "platform": "python",
        "level": "error",
        "fingerprint": [fingerprint],
        "exception": {"values": [{"type": exc_type, "value": message}]},
        "tags": {k: str(v)[:200] for k, v in tags.items()},
    }
    capture_event(event)
    time.sleep(0.05)


for filename, methodology, slug in RUNS:
    try:
        d = load(filename)
    except FileNotFoundError:
        print(f"  skip {filename} (not found)")
        continue

    meta = d.get("meta", {})
    agg = d.get("aggregate", {})
    arms = agg.get("arms", {})
    on = arms.get("on", {})
    off = arms.get("off", {})
    safety = agg.get("safety", {})
    calibration = d.get("calibration", {})

    timestamp = meta.get("timestamp", "2026-06-21T10:00:00+00:00")
    model = meta.get("proposer") or meta.get("backend") or meta.get("model", "unknown")
    run_id = meta.get("run_id", slug)
    tasks = meta.get("tasks") or len(meta.get("cases", meta.get("task_names", [])))
    trials = meta.get("trials", 1)

    clr_on = on.get("correct_landed_rate")
    clr_off = off.get("correct_landed_rate")
    escalations = on.get("escalations", 0)
    regressions = on.get("regressions_shipped", 0)
    false_rej = safety.get("false_rejection_rate")

    base_tags = {
        "component": "benchmark",
        "phase": "completed",
        "methodology": methodology,
        "model": model,
        "run_id": run_id,
        "git_revision": "main",
    }

    print(f"\n{filename}")
    print(f"  ts={timestamp[:19]}  ON clr={clr_on}  esc={escalations}  false_rej={false_rej}")

    # ── Calibration failure (gates rejected known-good code) ──────────────
    if calibration and not calibration.get("valid", True):
        failed = calibration.get("failed", [])
        send_exception(
            f"{slug}-calibration-failed",
            "AssertionError",
            f"calibration FAILED: {len(failed)} known-good tasks rejected by gate stack: {failed}",
            timestamp,
            {**base_tags, "status": "calibration-failed"},
        )
        print(f"  → calibration failure ({len(failed)} tasks)")

    # ── High false-rejection rate ─────────────────────────────────────────
    if false_rej is not None and false_rej >= 0.5:
        send_exception(
            f"{slug}-false-rejection-rate",
            "AssertionError",
            f"gate stack false-rejection rate={false_rej:.0%} on {tasks} tasks × {trials} trials "
            f"— gates are over-rejecting correct refactors (ON clr={clr_on:.0%}, escalations={escalations})",
            timestamp,
            {**base_tags, "status": "regressed", "gate": "typecheck"},
        )
        print(f"  → false rejection rate {false_rej:.0%}")

    # ── ON arm well below OFF arm ─────────────────────────────────────────
    if clr_on is not None and clr_off is not None and clr_on < clr_off - 0.2:
        send_exception(
            f"{slug}-on-arm-underperforms-off",
            "AssertionError",
            f"ON arm ({clr_on:.0%}) significantly underperforms OFF arm ({clr_off:.0%}) "
            f"— Refactorika gates are making outcomes WORSE, not better",
            timestamp,
            {**base_tags, "status": "regressed"},
        )
        print(f"  → ON {clr_on:.0%} vs OFF {clr_off:.0%}")

    # ── Regressions shipped ───────────────────────────────────────────────
    if regressions and regressions > 0:
        send_exception(
            f"{slug}-regressions-shipped",
            "AssertionError",
            f"{regressions} behavior-breaking change(s) passed all gates and were committed "
            f"— safety invariant violated",
            timestamp,
            {**base_tags, "status": "regressed", "gate": "tests"},
        )
        print(f"  → {regressions} regressions shipped")

    # ── Mass escalations (gate stack unusable) ────────────────────────────
    if escalations and escalations >= 5:
        send_exception(
            f"{slug}-mass-escalations",
            "RuntimeError",
            f"{escalations}/{on.get('runs', '?')} ON-arm runs escalated to 'needs-human' "
            f"— gate stack is blocking the agent on nearly every task",
            timestamp,
            {**base_tags, "status": "skipped-needs-human"},
        )
        print(f"  → {escalations} escalations")

    # ── Zero correct on OFF arm (benchmark itself broken) ─────────────────
    if clr_off is not None and clr_off == 0.0 and (tasks or 0) > 0:
        send_exception(
            f"{slug}-off-arm-zero",
            "AssertionError",
            f"OFF arm correct_landed_rate=0.0 — unmodified code fails oracle tests, "
            f"benchmark setup invalid or test harness broken",
            timestamp,
            {**base_tags, "status": "calibration-failed"},
        )
        print(f"  → OFF arm zero (harness broken)")

    # ── Successful run warning (positive signal) ──────────────────────────
    if clr_on is not None and clr_on >= 0.9 and regressions == 0:
        send_event(
            f"{slug}-on-arm-healthy",
            "info",
            f"benchmark healthy: ON clr={clr_on:.0%}, regressions=0, "
            f"escalations={escalations} — gate stack operating correctly",
            timestamp,
            {**base_tags, "status": "committed"},
        )
        print(f"  → healthy run (info)")

    # ── The pivot decision event ──────────────────────────────────────────
    if slug == "harness-full-disaster":
        send_exception(
            "pivot-decision-harness-approach-abandoned",
            "RuntimeError",
            "harness-based benchmark approach abandoned: 90% false-rejection rate makes ON arm "
            "worse than no gate at all (ON clr=10% vs OFF clr=100%). "
            "Pivoting to full-system evaluation methodology.",
            timestamp,
            {**base_tags, "status": "skipped-needs-human", "phase": "pivot"},
        )
        print(f"  → PIVOT DECISION EVENT sent")


print("\nDone. All benchmark runs replayed into Sentry with original timestamps.")
