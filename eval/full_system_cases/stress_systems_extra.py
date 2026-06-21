"""System-boundary stress cases for full-system refactoring evaluation."""

# Fixture source is embedded verbatim; wrapping it would distort generated repositories.
# ruff: noqa: E501

from __future__ import annotations

from .stress import StressCase, StressExpectation

_INIT = {"app/__init__.py": "", "tests/__init__.py": ""}


ASYNC_CANCELLATION = StressCase(
    name="async_cancellation_releases_lease",
    category="async-resource-safety",
    rationale="Shared async lifecycle code must retain cancellation-safe cleanup.",
    baseline_files={
        **_INIT,
        "app/jobs.py": '''import asyncio


class Lease:
    def __init__(self) -> None:
        self.active = False

    async def acquire(self) -> None:
        self.active = True

    async def release(self) -> None:
        self.active = False


async def run_import(lease: Lease, ready: asyncio.Event) -> str:
    await lease.acquire()
    try:
        await ready.wait()
        return "imported"
    finally:
        await lease.release()


async def run_export(lease: Lease, ready: asyncio.Event) -> str:
    await lease.acquire()
    try:
        await ready.wait()
        return "exported"
    finally:
        await lease.release()
''',
        "ARCHITECTURE.md": "Async lease lifecycle belongs in one private helper. Cancellation must release acquired leases.\n",
        "tests/test_jobs.py": '''import asyncio

from app.jobs import Lease, run_import


def test_completed_job_releases_lease() -> None:
    async def scenario() -> None:
        lease = Lease()
        ready = asyncio.Event()
        ready.set()
        assert await run_import(lease, ready) == "imported"
        assert lease.active is False

    asyncio.run(scenario())
''',
    },
    hidden_tests='''import asyncio
import contextlib

from app.jobs import Lease, run_export


def test_cancellation_releases_lease() -> None:
    async def scenario() -> None:
        lease = Lease()
        blocked = asyncio.Event()
        task = asyncio.create_task(run_export(lease, blocked))
        while not lease.active:
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert lease.active is False

    asyncio.run(scenario())
''',
    expectations=(
        StressExpectation("calls_private", "app/jobs.py", "run_import"),
        StressExpectation("calls_private", "app/jobs.py", "run_export"),
    ),
)


ASYNC_ORDER = StressCase(
    name="async_gather_preserves_input_order",
    category="async-ordering",
    rationale="Extracting concurrent collection work must preserve positional results.",
    baseline_files={
        **_INIT,
        "app/batches.py": '''import asyncio


async def delayed(value: str, delay: float) -> str:
    await asyncio.sleep(delay)
    return value


async def load_names(rows: list[tuple[str, float]]) -> list[str]:
    tasks = [delayed(value, delay) for value, delay in rows]
    return list(await asyncio.gather(*tasks))


async def load_codes(rows: list[tuple[str, float]]) -> list[str]:
    tasks = [delayed(value, delay) for value, delay in rows]
    return list(await asyncio.gather(*tasks))
''',
        "ARCHITECTURE.md": "Concurrent ordered collection loading is implemented once in a private helper.\n",
        "tests/test_batches.py": '''import asyncio

from app.batches import load_names


def test_names() -> None:
    assert asyncio.run(load_names([("a", 0), ("b", 0)])) == ["a", "b"]
''',
    },
    hidden_tests='''import asyncio

from app.batches import load_codes, load_names


def test_completion_timing_does_not_reorder_results() -> None:
    rows = [("slow", 0.02), ("fast", 0), ("middle", 0.01)]
    assert asyncio.run(load_names(rows)) == ["slow", "fast", "middle"]
    assert asyncio.run(load_codes(rows)) == ["slow", "fast", "middle"]
''',
    expectations=(
        StressExpectation("calls_private", "app/batches.py", "load_names"),
        StressExpectation("calls_private", "app/batches.py", "load_codes"),
    ),
)


TRANSACTION_ROLLBACK = StressCase(
    name="transaction_failure_restores_snapshot",
    category="transaction-semantics",
    rationale="Factoring transaction mechanics must not commit partial mutations.",
    baseline_files={
        **_INIT,
        "app/ledger.py": '''class Ledger:
    def __init__(self, balance: int) -> None:
        self.balance = balance

    def transfer(self, amount: int, fail: bool = False) -> None:
        snapshot = self.balance
        try:
            self.balance -= amount
            if fail:
                raise RuntimeError("transfer failed")
        except Exception:
            self.balance = snapshot
            raise

    def refund(self, amount: int, fail: bool = False) -> None:
        snapshot = self.balance
        try:
            self.balance += amount
            if fail:
                raise RuntimeError("refund failed")
        except Exception:
            self.balance = snapshot
            raise
''',
        "ARCHITECTURE.md": "Snapshot/rollback transaction policy belongs in a private helper; mutations remain caller-specific.\n",
        "tests/test_ledger.py": "from app.ledger import Ledger\n\n\ndef test_transfer() -> None:\n    ledger = Ledger(20)\n    ledger.transfer(4)\n    assert ledger.balance == 16\n",
    },
    hidden_tests='''import pytest

from app.ledger import Ledger


@pytest.mark.parametrize(("method", "amount"), [("transfer", 4), ("refund", 7)])
def test_failed_transaction_rolls_back(method: str, amount: int) -> None:
    ledger = Ledger(20)
    with pytest.raises(RuntimeError):
        getattr(ledger, method)(amount, fail=True)
    assert ledger.balance == 20
''',
    expectations=(
        StressExpectation("calls_private", "app/ledger.py", "transfer"),
        StressExpectation("calls_private", "app/ledger.py", "refund"),
    ),
)


SERIALIZATION_MISSING = StressCase(
    name="serialization_distinguishes_null_and_missing",
    category="serialization-contract",
    rationale="A shared serializer must omit missing values while retaining explicit null.",
    baseline_files={
        **_INIT,
        "app/payloads.py": '''MISSING = object()


def profile_payload(name: str, nickname: object = MISSING) -> dict[str, object]:
    payload: dict[str, object] = {"name": name}
    if nickname is not MISSING:
        payload["nickname"] = nickname
    return payload


def account_payload(account_id: int, note: object = MISSING) -> dict[str, object]:
    payload: dict[str, object] = {"id": account_id}
    if note is not MISSING:
        payload["note"] = note
    return payload
''',
        "ARCHITECTURE.md": "Optional-field serialization is shared; MISSING omits and None serializes as null.\n",
        "tests/test_payloads.py": "from app.payloads import profile_payload\n\n\ndef test_missing_is_omitted() -> None:\n    assert profile_payload('Ada') == {'name': 'Ada'}\n",
    },
    hidden_tests='''from app.payloads import account_payload, profile_payload


def test_explicit_none_is_not_missing() -> None:
    assert profile_payload("Ada", None) == {"name": "Ada", "nickname": None}
    assert account_payload(7, None) == {"id": 7, "note": None}
    assert account_payload(7) == {"id": 7}
''',
    expectations=(
        StressExpectation("calls_private", "app/payloads.py", "profile_payload"),
        StressExpectation("calls_private", "app/payloads.py", "account_payload"),
    ),
)


SERIALIZATION_TIMEZONE = StressCase(
    name="serialization_preserves_timezone_offsets",
    category="serialization-timezone",
    rationale="Datetime extraction must retain aware offsets and reject naive values.",
    baseline_files={
        **_INIT,
        "app/timestamps.py": '''from datetime import datetime


def created_payload(value: datetime) -> dict[str, str]:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    return {"created_at": value.isoformat()}


def updated_payload(value: datetime) -> dict[str, str]:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("updated_at must be timezone-aware")
    return {"updated_at": value.isoformat()}
''',
        "ARCHITECTURE.md": "Aware datetime serialization is centralized without normalizing away source offsets.\n",
        "tests/test_timestamps.py": "from datetime import datetime, timezone\n\nfrom app.timestamps import created_payload\n\n\ndef test_utc() -> None:\n    assert created_payload(datetime(2024, 1, 1, tzinfo=timezone.utc))['created_at'].endswith('+00:00')\n",
    },
    hidden_tests='''from datetime import datetime, timedelta, timezone

import pytest

from app.timestamps import created_payload, updated_payload


def test_offset_is_preserved_and_naive_is_rejected() -> None:
    value = datetime(2024, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert created_payload(value)["created_at"].endswith("+05:30")
    assert updated_payload(value)["updated_at"].endswith("+05:30")
    with pytest.raises(ValueError, match="updated_at"):
        updated_payload(datetime(2024, 1, 1))
''',
    expectations=(
        StressExpectation("calls_private", "app/timestamps.py", "created_payload"),
        StressExpectation("calls_private", "app/timestamps.py", "updated_payload"),
    ),
)


SERIALIZATION_ENUM = StressCase(
    name="serialization_uses_enum_wire_values",
    category="serialization-enum",
    rationale="Shared enum serialization must emit stable wire values rather than member names.",
    baseline_files={
        **_INIT,
        "app/enums.py": '''from enum import Enum


class Status(Enum):
    IN_PROGRESS = "in-progress"
    DONE = "done"


class Priority(Enum):
    HIGH = "high-priority"
    LOW = "low-priority"


def status_payload(value: Status) -> dict[str, str]:
    return {"status": value.value}


def priority_payload(value: Priority) -> dict[str, str]:
    return {"priority": value.value}
''',
        "ARCHITECTURE.md": "Enum wire-value conversion belongs in a private serialization helper.\n",
        "tests/test_enums.py": "from app.enums import Status, status_payload\n\n\ndef test_status() -> None:\n    assert status_payload(Status.DONE) == {'status': 'done'}\n",
    },
    hidden_tests='''from app.enums import Priority, Status, priority_payload, status_payload


def test_wire_values_not_python_names() -> None:
    assert status_payload(Status.IN_PROGRESS) == {"status": "in-progress"}
    assert priority_payload(Priority.HIGH) == {"priority": "high-priority"}
''',
    expectations=(
        StressExpectation("calls_private", "app/enums.py", "status_payload"),
        StressExpectation("calls_private", "app/enums.py", "priority_payload"),
    ),
)


FILESYSTEM_CLEANUP = StressCase(
    name="filesystem_atomic_write_cleans_temporary_file",
    category="filesystem-resource-safety",
    rationale="Extracted atomic writes must clean temporary files after replacement failures.",
    baseline_files={
        **_INIT,
        "app/writes.py": '''from pathlib import Path


def write_report(destination: Path, content: str) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_text(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_export(destination: Path, content: str) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_text(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
''',
        "ARCHITECTURE.md": "Atomic file replacement and unconditional temporary cleanup belong in one helper.\n",
        "tests/test_writes.py": "from app.writes import write_report\n\n\ndef test_write(tmp_path) -> None:\n    target = tmp_path / 'report.txt'\n    write_report(target, 'ok')\n    assert target.read_text() == 'ok'\n",
    },
    hidden_tests='''from pathlib import Path

import pytest

from app.writes import write_export, write_report


@pytest.mark.parametrize("writer", [write_report, write_export])
def test_failed_replace_leaves_no_temporary_file(tmp_path: Path, writer) -> None:
    destination = tmp_path / "occupied"
    destination.mkdir()
    with pytest.raises(OSError):
        writer(destination, "data")
    assert not (tmp_path / "occupied.tmp").exists()
''',
    expectations=(
        StressExpectation("calls_private", "app/writes.py", "write_report"),
        StressExpectation("calls_private", "app/writes.py", "write_export"),
    ),
)


FILESYSTEM_PATH = StressCase(
    name="filesystem_root_confinement_survives_refactor",
    category="filesystem-path-safety",
    rationale="Factoring path lookup must resolve traversal and symlink escapes before I/O.",
    baseline_files={
        **_INIT,
        "app/safe_files.py": '''from pathlib import Path


def load_text(root: Path, relative: str) -> str:
    base = root.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise ValueError("path escapes root")
    return target.read_text()


def load_bytes(root: Path, relative: str) -> bytes:
    base = root.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise ValueError("path escapes root")
    return target.read_bytes()
''',
        "ARCHITECTURE.md": "Root-confined path resolution is centralized and occurs before any file read.\n",
        "tests/test_safe_files.py": "from app.safe_files import load_text\n\n\ndef test_read(tmp_path) -> None:\n    (tmp_path / 'a.txt').write_text('a')\n    assert load_text(tmp_path, 'a.txt') == 'a'\n",
    },
    hidden_tests='''from pathlib import Path

import pytest

from app.safe_files import load_bytes, load_text


def test_traversal_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (root / "link").symlink_to(outside)
    for loader, path in [(load_text, "../secret.txt"), (load_bytes, "link")]:
        with pytest.raises(ValueError, match="escapes root"):
            loader(root, path)
''',
    expectations=(
        StressExpectation("calls_private", "app/safe_files.py", "load_text"),
        StressExpectation("calls_private", "app/safe_files.py", "load_bytes"),
    ),
)


MIDDLEWARE_ORDER = StressCase(
    name="middleware_wrapping_order_is_stable",
    category="framework-ordering",
    rationale="A shared middleware composer must preserve outside-in invocation order.",
    baseline_files={
        **_INIT,
        "app/middleware.py": '''from collections.abc import Callable

Handler = Callable[[list[str]], None]
Middleware = Callable[[Handler], Handler]


def request_pipeline(items: list[Middleware], terminal: Handler) -> Handler:
    handler = terminal
    for item in reversed(items):
        handler = item(handler)
    return handler


def response_pipeline(items: list[Middleware], terminal: Handler) -> Handler:
    handler = terminal
    for item in reversed(items):
        handler = item(handler)
    return handler
''',
        "ARCHITECTURE.md": "Middleware composition is shared. Registration order defines outside-in execution.\n",
        "tests/test_middleware.py": '''from app.middleware import request_pipeline


def test_empty_pipeline() -> None:
    events = []
    request_pipeline([], lambda log: log.append("terminal"))(events)
    assert events == ["terminal"]
''',
    },
    hidden_tests='''from app.middleware import request_pipeline, response_pipeline


def layer(name):
    def middleware(next_handler):
        def handle(events):
            events.append(name + ":before")
            next_handler(events)
            events.append(name + ":after")
        return handle
    return middleware


def test_registration_order_is_outside_in() -> None:
    expected = ["a:before", "b:before", "end", "b:after", "a:after"]
    for factory in (request_pipeline, response_pipeline):
        events = []
        factory([layer("a"), layer("b")], lambda log: log.append("end"))(events)
        assert events == expected
''',
    expectations=(
        StressExpectation("calls_private", "app/middleware.py", "request_pipeline"),
        StressExpectation("calls_private", "app/middleware.py", "response_pipeline"),
    ),
)


CACHE_RESOURCE_COUNTS = StressCase(
    name="cache_reuses_load_and_closes_source",
    category="performance-resource-safety",
    rationale="Factoring cache-through loading must retain one load and guaranteed close.",
    baseline_files={
        **_INIT,
        "app/repository.py": '''class Source:
    def __init__(self) -> None:
        self.loads = 0
        self.closes = 0

    def load(self, user_id: int) -> dict[str, str]:
        self.loads += 1
        return {"name": f"user-{user_id}", "email": f"{user_id}@example.test"}

    def close(self) -> None:
        self.closes += 1


class Repository:
    def __init__(self, source: Source) -> None:
        self.source = source
        self.cache: dict[int, dict[str, str]] = {}

    def user_name(self, user_id: int) -> str:
        if user_id not in self.cache:
            try:
                self.cache[user_id] = self.source.load(user_id)
            finally:
                self.source.close()
        return self.cache[user_id]["name"]

    def user_email(self, user_id: int) -> str:
        if user_id not in self.cache:
            try:
                self.cache[user_id] = self.source.load(user_id)
            finally:
                self.source.close()
        return self.cache[user_id]["email"]
''',
        "ARCHITECTURE.md": "Cache-through loading and source cleanup belong in one private Repository helper.\n",
        "tests/test_repository.py": "from app.repository import Repository, Source\n\n\ndef test_name() -> None:\n    assert Repository(Source()).user_name(3) == 'user-3'\n",
    },
    hidden_tests='''from app.repository import Repository, Source


def test_same_key_loads_and_closes_exactly_once() -> None:
    source = Source()
    repository = Repository(source)
    assert repository.user_name(4) == "user-4"
    assert repository.user_email(4) == "4@example.test"
    assert source.loads == 1
    assert source.closes == 1
''',
    expectations=(
        StressExpectation("calls_private", "app/repository.py", "user_name"),
        StressExpectation("calls_private", "app/repository.py", "user_email"),
    ),
)


STRESS_CASES: tuple[StressCase, ...] = (
    ASYNC_CANCELLATION,
    ASYNC_ORDER,
    TRANSACTION_ROLLBACK,
    SERIALIZATION_MISSING,
    SERIALIZATION_TIMEZONE,
    SERIALIZATION_ENUM,
    FILESYSTEM_CLEANUP,
    FILESYSTEM_PATH,
    MIDDLEWARE_ORDER,
    CACHE_RESOURCE_COUNTS,
)

CASES = STRESS_CASES
