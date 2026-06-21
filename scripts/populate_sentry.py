"""
Send representative error events to Sentry so the dashboard shows real issue classes.

Run from repo root:
    source .venv/bin/activate
    SENTRY_DSN=$(grep SENTRY_DSN .env | cut -d= -f2-) python scripts/populate_sentry.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import sentry_sdk

DSN = os.environ.get("SENTRY_DSN", "").strip()
if not DSN:
    sys.exit("SENTRY_DSN not set — export it or add to .env")


def _init(component: str) -> None:
    from refactorika.observability import scrub_event

    sentry_sdk.init(
        dsn=DSN,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=os.environ.get("SENTRY_RELEASE") or None,
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=0.0,
        before_send=scrub_event,
    )
    sentry_sdk.set_tag("component", component)


def _send(label: str, fn) -> None:
    print(f"  → {label} ...", end=" ", flush=True)
    try:
        fn()
        time.sleep(0.4)  # let the SDK flush between calls
        print("sent")
    except Exception:  # noqa: BLE001
        print("FAILED to send")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# MCP server errors
# ---------------------------------------------------------------------------
print("\n[mcp] tool-layer errors")
_init("mcp")


def _apply_bad_syntax():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("gate", "parse")
        scope.set_tag("status", "rolled-back")
        scope.set_tag("phase", "apply")
        try:
            raise SyntaxError("unexpected EOF while parsing edited file")
        except SyntaxError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


def _apply_type_error():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("gate", "typecheck")
        scope.set_tag("status", "rolled-back")
        scope.set_tag("phase", "apply")
        try:
            raise TypeError("pyright: argument of type 'int' is not assignable to 'str'")
        except TypeError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


def _apply_test_failure():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("gate", "tests")
        scope.set_tag("status", "rolled-back")
        scope.set_tag("phase", "apply")
        try:
            raise AssertionError("pytest: 3 tests failed after refactor — rolling back")
        except AssertionError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


def _tool_missing_file():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("phase", "analyze")
        scope.set_tag("status", "rolled-back")
        try:
            raise FileNotFoundError("analyze_file: target path does not exist")
        except FileNotFoundError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


def _retries_exhausted():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("phase", "apply")
        scope.set_tag("status", "skipped-needs-human")
        try:
            raise RuntimeError("apply_and_verify: max retries (3) exhausted — needs human review")
        except RuntimeError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


_send("parse gate — SyntaxError on edited file", _apply_bad_syntax)
_send("typecheck gate — pyright rejection", _apply_type_error)
_send("behavior gate — pytest failure triggers rollback", _apply_test_failure)
_send("analyze_file — missing target", _tool_missing_file)
_send("apply_and_verify — retries exhausted", _retries_exhausted)


# ---------------------------------------------------------------------------
# CLI errors
# ---------------------------------------------------------------------------
print("\n[cli] command-layer errors")
_init("cli")


def _cli_invalid_path():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("phase", "init")
        try:
            raise ValueError("--target must be a Python file or directory, got: ./not_python")
        except ValueError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


def _cli_redis_unavailable():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("phase", "memory")
        scope.set_tag("status", "degraded")
        try:
            raise ConnectionError("Redis unavailable — falling back to local JSON storage")
        except ConnectionError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


_send("invalid --target path", _cli_invalid_path)
_send("Redis unavailable — degraded to local JSON", _cli_redis_unavailable)


# ---------------------------------------------------------------------------
# Benchmark regressions
# ---------------------------------------------------------------------------
print("\n[benchmark] regression warnings")
_init("benchmark")

from refactorika.observability import capture_benchmark_regression

result_regressed = {
    "meta": {"run_id": "run-populate-001", "model": "claude-sonnet-4-6", "provider": "anthropic"},
    "aggregate": {
        "arms": {"on": {"correct_landed_rate": 0.62, "regressions_shipped": 2}}
    },
}
baseline = {
    "aggregate": {"arms": {"on": {"correct_landed_rate": 0.91, "regressions_shipped": 0}}}
}

result_ok = {
    "meta": {"run_id": "run-populate-002", "model": "claude-sonnet-4-6", "provider": "anthropic"},
    "aggregate": {
        "arms": {"on": {"correct_landed_rate": 0.93, "regressions_shipped": 0}}
    },
}


def _regression_fired():
    fired = capture_benchmark_regression(result_regressed, baseline, threshold=0.1)
    assert fired, "expected regression to fire"


def _regression_skipped():
    fired = capture_benchmark_regression(result_ok, baseline, threshold=0.1)
    assert not fired, "expected no regression"


_send("ON-arm correct_landed_rate dropped 29 pp + 2 regressions_shipped", _regression_fired)
_send("healthy run — no event fired (expected)", _regression_skipped)


# ---------------------------------------------------------------------------
# Dashboard errors
# ---------------------------------------------------------------------------
print("\n[dashboard] render errors")
_init("dashboard")


def _dashboard_missing_log():
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("phase", "render")
        try:
            raise KeyError("edit_log: 'diff' key missing from EditRecord — schema mismatch")
        except KeyError as exc:
            sentry_sdk.capture_exception(exc, scope=scope)


_send("EditRecord schema mismatch on render", _dashboard_missing_log)


# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
print("\nDone. All events sent — check your Sentry dashboard.")
