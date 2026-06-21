"""Privacy-safe, fail-open Sentry integration for product and benchmark entrypoints."""

from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

try:
    import sentry_sdk
except ImportError:  # pragma: no cover - exercised by deployments without the optional SDK
    sentry_sdk = None  # type: ignore[assignment]

_ALLOWED_TAGS = {
    "arm",
    "case",
    "component",
    "gate",
    "git_revision",
    "model",
    "phase",
    "provider",
    "release",
    "run_id",
    "status",
    "trial",
}
F = TypeVar("F", bound=Callable[..., Any])


def scrub_event(event: Event, hint: Hint) -> Event | None:
    """Remove payloads that could contain prompts, source, patches, paths, or secrets."""

    del hint
    mutable = cast(dict[str, Any], event)
    allowed_fields = {
        "environment",
        "event_id",
        "exception",
        "fingerprint",
        "level",
        "logger",
        "message",
        "platform",
        "release",
        "server_name",
        "tags",
        "timestamp",
    }
    for key in list(mutable):
        if key not in allowed_fields:
            mutable.pop(key, None)
    mutable["tags"] = {
        key: str(value)[:200]
        for key, value in mutable.get("tags", {}).items()
        if key in _ALLOWED_TAGS
    }
    exception = mutable.get("exception", {})
    for value in exception.get("values", []):
        value["value"] = value.get("type", "exception")
        value.pop("stacktrace", None)
    if "message" in mutable:
        mutable["message"] = "benchmark_regression"
    return event


def init_sentry(component: str) -> bool:
    """Initialize errors-only telemetry when configured; never fail the caller."""

    if sentry_sdk is None or not (dsn := os.environ.get("SENTRY_DSN")):
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENVIRONMENT"),
            release=os.environ.get("SENTRY_RELEASE"),
            send_default_pii=False,
            include_local_variables=False,
            traces_sample_rate=0.0,
            before_send=scrub_event,
        )
        sentry_sdk.set_tag("component", component)
        return True
    except Exception:  # noqa: BLE001 - observability must never break product behavior
        return False


def capture_exception(
    error: BaseException, *, component: str, phase: str, tags: dict[str, object] | None = None
) -> None:
    if sentry_sdk is None or not os.environ.get("SENTRY_DSN"):
        return
    try:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("component", component)
            scope.set_tag("phase", phase)
            for key, value in (tags or {}).items():
                if key in _ALLOWED_TAGS:
                    scope.set_tag(key, str(value)[:200])
            sentry_sdk.capture_exception(error)
    except Exception:  # noqa: BLE001
        pass


def report_exceptions(component: str, phase: str) -> Callable[[F], F]:
    def decorator(function: F) -> F:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                capture_exception(exc, component=component, phase=phase)
                raise

        return wrapped  # type: ignore[return-value]

    return decorator


def capture_benchmark_regression(
    result: dict,
    baseline: dict,
    *,
    threshold: float,
) -> bool:
    """Emit one sanitized warning for a material ON-arm regression."""

    if sentry_sdk is None or not os.environ.get("SENTRY_DSN"):
        return False
    try:
        current = result["aggregate"]["arms"]["on"]
        previous = baseline["aggregate"]["arms"]["on"]
        rate_drop = float(previous["correct_landed_rate"]) - float(current["correct_landed_rate"])
        regressed = rate_drop > threshold or int(current["regressions_shipped"]) > 0
        if not regressed:
            return False
        with sentry_sdk.push_scope() as scope:
            meta = result.get("meta", {})
            for key in ("run_id", "model", "provider", "release", "git_revision"):
                if value := meta.get(key):
                    scope.set_tag(key, str(value)[:200])
            scope.set_tag("component", "benchmark")
            scope.set_tag("phase", "completed")
            scope.set_tag("status", "regressed")
            sentry_sdk.capture_message("benchmark_regression", level="warning")
        return True
    except Exception as exc:  # malformed baselines are operational failures
        capture_exception(exc, component="benchmark", phase="baseline_comparison")
        return False
