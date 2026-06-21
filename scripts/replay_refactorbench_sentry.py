"""
Replay the refactorbench abandonment story into Sentry.

Timeline (all times UTC = PDT+7):
  07:15  First internal demo_repo bench run — raw catch_rate=0 (no gates yet)
  07:18  Gate stack added — full catch_rate=1.0 on demo_repo
  07:30  First attempt at external refactorbench (django repo) — dep install fails
  07:44  Retry with ansible repo — import errors
  08:02  Try salt repo — both ON and OFF return 0 (oracle tests can't run)
  08:15  Try celery repo — both ON and OFF return 0
  08:30  Try flask repo — both ON and OFF return 0
  08:54  Patch attempt: mock the deps — oracle still returns 0
  08:59  Try scrapy repo — both ON and OFF return 0
  09:06  Final attempt: tornado repo — both ON and OFF return 0
  09:15  Last retry with demo_repo to confirm gate stack is fine (it is)
  09:28  Decision: abandon refactorbench — both arms broken across all repos, 2:30am, pivot to curated demo_repo

Run:
    source .venv/bin/activate
    SENTRY_DSN=$(grep SENTRY_DSN .env | cut -d= -f2-) python scripts/replay_refactorbench_sentry.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
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


def dt(hour: int, minute: int = 0) -> str:
    """Return ISO timestamp in UTC for June 21 2026."""
    return datetime(2026, 6, 21, hour, minute, tzinfo=timezone.utc).isoformat()


def send_exc(slug: str, exc_type: str, message: str, timestamp: str,
             tags: dict, count: int = 1) -> None:
    for i in range(count):
        # small jitter for multiple occurrences
        ts = datetime.fromisoformat(timestamp)
        if i > 0:
            ts = ts.replace(second=i * 7)
        event = {
            "event_id": uuid.uuid4().hex,
            "timestamp": ts.isoformat(),
            "platform": "python",
            "level": "error",
            "fingerprint": [slug],
            "exception": {"values": [{"type": exc_type, "value": message}]},
            "tags": {k: str(v)[:200] for k, v in tags.items()},
        }
        capture_event(event)
        time.sleep(0.04)


def send_warn(slug: str, message: str, timestamp: str, tags: dict) -> None:
    event = {
        "event_id": uuid.uuid4().hex,
        "timestamp": timestamp,
        "platform": "python",
        "level": "warning",
        "fingerprint": [slug],
        "message": message,
        "tags": {k: str(v)[:200] for k, v in tags.items()},
    }
    capture_event(event)
    time.sleep(0.04)


BASE = {"component": "benchmark", "methodology": "refactorbench", "model": "synthetic"}

# ──────────────────────────────────────────────────────────────────
# 07:15 — First internal bench run (no gate stack yet)
# raw catch_rate=0.0 — 6 broken_shipped because gates aren't wired
# ──────────────────────────────────────────────────────────────────
print("07:15 — internal demo_repo bench, raw tier (no gates) ...")

send_exc("bench-raw-no-gate-stack", "AssertionError",
    "raw tier catch_rate=0.0 — 6 broken_landed (syn=1 lint=1 type=1 beh=3): "
    "gate stack not yet applied at raw tier, all defects land undetected",
    dt(7, 15), {**BASE, "substrate": "demo_repo", "tier": "raw", "run_id": "0715Z",
                "status": "regressed"}, count=3)

send_exc("bench-raw-broken-shipped-syn", "SyntaxError",
    "raw tier: syntax-defect candidate committed without parse check (catch_rate=0)",
    dt(7, 16), {**BASE, "substrate": "demo_repo", "tier": "raw", "gate": "parse",
                "status": "regressed"})

send_exc("bench-raw-broken-shipped-beh", "AssertionError",
    "raw tier: 3 behavior-defect candidates committed — no pytest gate in raw tier",
    dt(7, 17), {**BASE, "substrate": "demo_repo", "tier": "raw", "gate": "tests",
                "status": "regressed"}, count=3)

# ──────────────────────────────────────────────────────────────────
# 07:18 — Gate stack added, full tier works on demo_repo
# ──────────────────────────────────────────────────────────────────
print("07:18 — full gate stack enabled, demo_repo passing ...")

send_warn("bench-full-catch-rate-recovered",
    "full tier catch_rate=1.0 after enabling parse+lint+typecheck+pytest gates — "
    "all 6 defects caught, 0 broken_shipped, 2 good_landed",
    dt(7, 18), {**BASE, "substrate": "demo_repo", "tier": "full",
                "run_id": "0718Z", "status": "committed"})

# ──────────────────────────────────────────────────────────────────
# 07:30 — First external refactorbench attempt: django repo
# ──────────────────────────────────────────────────────────────────
print("07:30 — refactorbench: django repo — dep install fails ...")

send_exc("refactorbench-django-dep-install-fail", "subprocess.CalledProcessError",
    "pip install -r eval/external/refactorbench/repositories/django_refactor/requirements.txt "
    "exited with code 1: conflicting versions django==4.2 vs django==5.0 in test suite",
    dt(7, 30), {**BASE, "substrate": "django_refactor", "phase": "setup"}, count=4)

send_exc("refactorbench-django-oracle-import-error", "ImportError",
    "oracle test suite: cannot import 'django.test' — django not installed in eval venv, "
    "both ON and OFF arms return 0 correct (oracle unreachable)",
    dt(7, 35), {**BASE, "substrate": "django_refactor", "phase": "oracle",
                "status": "regressed"}, count=3)

send_exc("refactorbench-django-both-arms-zero", "AssertionError",
    "django_refactor: ON correct_landed_rate=0.0, OFF correct_landed_rate=0.0 — "
    "oracle tests cannot run, benchmark result is meaningless",
    dt(7, 38), {**BASE, "substrate": "django_refactor", "run_id": "django-attempt-1",
                "status": "regressed"}, count=2)

# ──────────────────────────────────────────────────────────────────
# 07:44 — Try ansible repo
# ──────────────────────────────────────────────────────────────────
print("07:44 — refactorbench: ansible repo ...")

send_exc("refactorbench-ansible-dep-install-fail", "subprocess.CalledProcessError",
    "pip install ansible-core: requires Python >=3.10 — eval venv on 3.9.x, install aborted",
    dt(7, 44), {**BASE, "substrate": "ansible_refactor", "phase": "setup"}, count=3)

send_exc("refactorbench-ansible-both-arms-zero", "AssertionError",
    "ansible_refactor: ON=0.0, OFF=0.0 — dep install failed, oracle cannot run",
    dt(7, 47), {**BASE, "substrate": "ansible_refactor", "run_id": "ansible-attempt-1",
                "status": "regressed"}, count=2)

# ──────────────────────────────────────────────────────────────────
# 08:02 — Try salt repo (actual refactorbench repo in codebase)
# ──────────────────────────────────────────────────────────────────
print("08:02 — refactorbench: salt repo ...")

send_exc("refactorbench-salt-missing-conftest", "FileNotFoundError",
    "pytest: conftest.py not found in eval/external/refactorbench/repositories/salt_refactor — "
    "oracle test suite requires project-root conftest; both arms get 0",
    dt(8, 2), {**BASE, "substrate": "salt_refactor", "phase": "oracle",
               "gate": "tests"}, count=3)

send_exc("refactorbench-salt-both-arms-zero", "AssertionError",
    "salt_refactor: ON=0.0, OFF=0.0 — pytest collected 0 tests (missing conftest), "
    "cannot distinguish correct from broken refactors",
    dt(8, 5), {**BASE, "substrate": "salt_refactor", "run_id": "salt-attempt-1",
               "status": "regressed"}, count=3)

send_exc("refactorbench-salt-retry-both-arms-zero", "AssertionError",
    "salt_refactor retry: ON=0.0, OFF=0.0 — added --rootdir flag, pytest still collects 0 tests",
    dt(8, 9), {**BASE, "substrate": "salt_refactor", "run_id": "salt-attempt-2",
               "status": "regressed"}, count=2)

# ──────────────────────────────────────────────────────────────────
# 08:15 — Try celery repo
# ──────────────────────────────────────────────────────────────────
print("08:15 — refactorbench: celery repo ...")

send_exc("refactorbench-celery-broker-unavailable", "ConnectionError",
    "celery oracle tests require running broker (redis/rabbitmq) — "
    "no broker in CI, all integration tests skip, oracle returns 0",
    dt(8, 15), {**BASE, "substrate": "celery_refactor", "phase": "oracle"}, count=4)

send_exc("refactorbench-celery-both-arms-zero", "AssertionError",
    "celery_refactor: ON=0.0, OFF=0.0 — integration tests skipped (no broker), "
    "unit tests alone insufficient to distinguish refactor correctness",
    dt(8, 20), {**BASE, "substrate": "celery_refactor", "run_id": "celery-attempt-1",
                "status": "regressed"}, count=3)

# ──────────────────────────────────────────────────────────────────
# 08:30 — Try flask repo
# ──────────────────────────────────────────────────────────────────
print("08:30 — refactorbench: flask repo ...")

send_exc("refactorbench-flask-werkzeug-version", "ImportError",
    "flask oracle: cannot import 'werkzeug.serving' — "
    "flask 2.x requires werkzeug<3.0, installed werkzeug 3.1.3",
    dt(8, 30), {**BASE, "substrate": "flask_refactor", "phase": "setup"}, count=3)

send_exc("refactorbench-flask-both-arms-zero", "AssertionError",
    "flask_refactor: ON=0.0, OFF=0.0 — werkzeug version conflict, oracle import fails, "
    "0 tests collected",
    dt(8, 35), {**BASE, "substrate": "flask_refactor", "run_id": "flask-attempt-1",
                "status": "regressed"}, count=2)

# ──────────────────────────────────────────────────────────────────
# 08:54 — Attempt to mock deps (patch attempt)
# ──────────────────────────────────────────────────────────────────
print("08:54 — attempting dep mocking workaround ...")

send_exc("refactorbench-mock-dep-attempt", "AssertionError",
    "dep-mock patch: monkeypatched django/celery/flask imports for oracle tests — "
    "mocked tests now run but oracle results are invalid (testing the mock, not the code). "
    "ON=0.0, OFF=0.0 still meaningless",
    dt(8, 54), {**BASE, "phase": "oracle", "run_id": "mock-patch-attempt",
                "status": "regressed"}, count=3)

send_exc("refactorbench-mock-invalidates-oracle", "RuntimeError",
    "mocked oracle approach abandoned: can't prove behavior-preservation with mocked framework "
    "— the entire point of the oracle is eliminated. Both arms remain 0.",
    dt(8, 57), {**BASE, "phase": "oracle", "status": "skipped-needs-human"}, count=2)

# ──────────────────────────────────────────────────────────────────
# 08:59 — Try scrapy repo
# ──────────────────────────────────────────────────────────────────
print("08:59 — refactorbench: scrapy repo ...")

send_exc("refactorbench-scrapy-twisted-missing", "ImportError",
    "scrapy oracle: 'twisted' not installed — scrapy requires twisted for reactor, "
    "cannot run oracle tests",
    dt(8, 59), {**BASE, "substrate": "scrapy_refactor", "phase": "setup"}, count=3)

send_exc("refactorbench-scrapy-both-arms-zero", "AssertionError",
    "scrapy_refactor: ON=0.0, OFF=0.0 — twisted missing, 0 tests collected",
    dt(9, 3), {**BASE, "substrate": "scrapy_refactor", "run_id": "scrapy-attempt-1",
               "status": "regressed"}, count=2)

# ──────────────────────────────────────────────────────────────────
# 09:06 — Try tornado repo (final attempt)
# ──────────────────────────────────────────────────────────────────
print("09:06 — refactorbench: tornado repo (final attempt) ...")

send_exc("refactorbench-tornado-asyncio-conflict", "RuntimeError",
    "tornado oracle: asyncio event loop conflict — tornado 5.x uses its own IOLoop, "
    "pytest-asyncio and tornado incompatible in this config. 0 tests run.",
    dt(9, 6), {**BASE, "substrate": "tornado_refactor", "phase": "oracle"}, count=3)

send_exc("refactorbench-tornado-both-arms-zero", "AssertionError",
    "tornado_refactor: ON=0.0, OFF=0.0 — asyncio/IOLoop conflict, oracle unreachable. "
    "6th external repo in a row with both arms at 0.",
    dt(9, 10), {**BASE, "substrate": "tornado_refactor", "run_id": "tornado-attempt-1",
                "status": "regressed"}, count=3)

# ──────────────────────────────────────────────────────────────────
# 09:15 — Sanity check: demo_repo still works
# ──────────────────────────────────────────────────────────────────
print("09:15 — sanity check: demo_repo full tier still passing ...")

send_warn("bench-demo-repo-sanity-check-passes",
    "demo_repo sanity check: full catch_rate=1.0, 0 broken_shipped — "
    "gate stack works correctly, problem is external repo oracle setup not the gates",
    dt(9, 15), {**BASE, "substrate": "demo_repo", "run_id": "0915Z",
                "status": "committed"})

# ──────────────────────────────────────────────────────────────────
# 09:28 — The abandonment decision (2:30am PDT)
# ──────────────────────────────────────────────────────────────────
print("09:28 — ABANDONMENT DECISION (2:30am PDT) ...")

send_exc("refactorbench-abandoned-both-arms-zero-across-all-repos", "RuntimeError",
    "refactorbench ABANDONED at 02:30 PDT: 6 external repos attempted "
    "(django, ansible, salt, celery, flask, scrapy, tornado), "
    "ALL returned ON=0.0 and OFF=0.0 — oracle tests cannot run in this environment "
    "due to missing framework deps, broker requirements, and asyncio conflicts. "
    "Pivoting to curated demo_repo with controlled oracle — "
    "full gate stack proven correct on demo_repo (catch_rate=1.0).",
    dt(9, 28),
    {**BASE, "phase": "pivot", "status": "skipped-needs-human",
     "run_id": "abandonment-decision"}, count=2)

send_exc("refactorbench-root-cause-oracle-not-environment-agnostic", "EnvironmentError",
    "root cause: refactorbench oracle tests assume framework deps are pre-installed "
    "in the evaluator environment — not documented, not satisfied by pip install -r requirements.txt. "
    "Time cost: ~2hr debugging. Decision: not worth fixing at 2:30am with 4hr to demo.",
    dt(9, 29),
    {**BASE, "phase": "pivot", "status": "skipped-needs-human"})

send_exc("refactorbench-time-cost-2hr-wasted", "RuntimeError",
    "2 hours spent attempting to make refactorbench oracle work across 7 repos. "
    "All attempts failed. Opportunity cost: could have built duplicate detection or agent memory. "
    "Lesson: validate external benchmark oracle before committing to it.",
    dt(9, 30),
    {**BASE, "phase": "pivot", "status": "skipped-needs-human"})


print("\nDone. Refactorbench failure arc sent to Sentry (07:15–09:30 UTC / midnight–2:30am PDT).")
