"""Behavior-preservation cases for the full-system refactoring benchmark.

Only ``baseline_files`` are materialized for an agent run. ``hidden_tests`` and
``trap_edits`` belong to the evaluator and must never be included in an agent or
harness prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

GENERIC_USER_PROMPT = "refactor this codebase"


@dataclass(frozen=True)
class StructuralExpectation:
    """A refactoring property scored independently from behavioral correctness."""

    description: str
    target_path: str
    evidence: str


@dataclass(frozen=True)
class BehaviorCase:
    name: str
    edge_case: str
    baseline_files: dict[str, str]
    hidden_tests: dict[str, str]
    structural_expectations: tuple[StructuralExpectation, ...]
    trap_edits: dict[str, str]
    user_prompt: str = GENERIC_USER_PROMPT

    def materialize(self, destination: Path) -> Path:
        """Write only agent-visible files to ``destination``."""

        destination.mkdir(parents=True, exist_ok=True)
        for relative, content in self.baseline_files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return destination


_COMMON = {
    "app/__init__.py": "",
    "tests/__init__.py": "",
}


ROUNDING = BehaviorCase(
    name="rounding_order",
    edge_case="Integer rounding changes when tax and discount operations are reordered.",
    baseline_files={
        **_COMMON,
        "app/pricing.py": """def standard_total(subtotal_cents: int) -> int:
    if subtotal_cents < 0:
        raise ValueError("subtotal must be non-negative")
    discounted = subtotal_cents * 85 // 100
    return discounted + discounted * 725 // 10_000


def member_total(subtotal_cents: int) -> int:
    if subtotal_cents < 0:
        raise ValueError("subtotal must be non-negative")
    discounted = subtotal_cents * 80 // 100
    return discounted + discounted * 725 // 10_000
""",
        "tests/test_pricing.py": """import pytest

from app.pricing import member_total, standard_total


def test_typical_totals() -> None:
    assert standard_total(10_000) == 9_116
    assert member_total(10_000) == 8_580


def test_negative_subtotal_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        standard_total(-1)
""",
    },
    hidden_tests={
        "tests/oracle/test_pricing_boundaries.py": """import pytest

from app.pricing import member_total, standard_total


@pytest.mark.parametrize(
    ("subtotal", "standard", "member"),
    [(0, 0, 0), (1, 0, 0), (99, 90, 84), (101, 91, 85), (199, 181, 170)],
)
def test_rounding_boundaries(subtotal: int, standard: int, member: int) -> None:
    assert standard_total(subtotal) == standard
    assert member_total(subtotal) == member


def test_member_validation_is_preserved() -> None:
    with pytest.raises(ValueError, match="subtotal must be non-negative"):
        member_total(-1)
""",
    },
    structural_expectations=(
        StructuralExpectation(
            "The duplicated validation and price calculation are consolidated.",
            "app/pricing.py",
            "Both public functions delegate to one private calculation helper.",
        ),
        StructuralExpectation(
            "Discount remains applied before tax, with integer truncation at each step.",
            "app/pricing.py",
            "The shared helper computes discounted cents before computing tax.",
        ),
    ),
    trap_edits={
        "app/pricing.py": """def _total(subtotal_cents: int, discount: int) -> int:
    if subtotal_cents < 0:
        raise ValueError("subtotal must be non-negative")
    taxed = subtotal_cents + subtotal_cents * 725 // 10_000
    return taxed * discount // 100


def standard_total(subtotal_cents: int) -> int:
    return _total(subtotal_cents, 85)


def member_total(subtotal_cents: int) -> int:
    return _total(subtotal_cents, 80)
""",
    },
)


GUARD_CLAUSES = BehaviorCase(
    name="guard_clause_continue",
    edge_case="Flattening nested loop logic can replace continue with an early function return.",
    baseline_files={
        **_COMMON,
        "app/events.py": """from collections.abc import Iterable


def billable_event_ids(events: Iterable[dict[str, object]]) -> list[str]:
    selected: list[str] = []
    for event in events:
        if event.get("enabled") is True:
            if event.get("kind") != "heartbeat":
                event_id = event.get("id")
                if isinstance(event_id, str) and event_id:
                    selected.append(event_id)
    return selected
""",
        "tests/test_events.py": """from app.events import billable_event_ids


def test_selects_a_billable_event() -> None:
    events = [{"id": "evt-1", "enabled": True, "kind": "charge"}]
    assert billable_event_ids(events) == ["evt-1"]


def test_ignores_a_heartbeat() -> None:
    events = [{"id": "pulse", "enabled": True, "kind": "heartbeat"}]
    assert billable_event_ids(events) == []
""",
    },
    hidden_tests={
        "tests/oracle/test_event_control_flow.py": """from app.events import billable_event_ids


def test_invalid_item_does_not_abort_later_items() -> None:
    events = [
        {"id": "off", "enabled": False, "kind": "charge"},
        {"id": "evt-2", "enabled": True, "kind": "charge"},
    ]
    assert billable_event_ids(events) == ["evt-2"]


def test_each_guard_only_skips_its_current_item() -> None:
    events = [
        {"id": "pulse", "enabled": True, "kind": "heartbeat"},
        {"id": "", "enabled": True, "kind": "charge"},
        {"id": 7, "enabled": True, "kind": "charge"},
        {"id": "evt-3", "enabled": True, "kind": "charge"},
        {"id": "evt-4", "enabled": True, "kind": "refund"},
    ]
    assert billable_event_ids(events) == ["evt-3", "evt-4"]
""",
    },
    structural_expectations=(
        StructuralExpectation(
            "The triple-nested condition is flattened without changing loop control flow.",
            "app/events.py",
            "Invalid events use loop-level guard clauses (continue), not function return.",
        ),
    ),
    trap_edits={
        "app/events.py": """from collections.abc import Iterable


def billable_event_ids(events: Iterable[dict[str, object]]) -> list[str]:
    selected: list[str] = []
    for event in events:
        if event.get("enabled") is not True:
            return selected
        if event.get("kind") == "heartbeat":
            continue
        event_id = event.get("id")
        if isinstance(event_id, str) and event_id:
            selected.append(event_id)
    return selected
""",
    },
)


NEAR_DUPLICATES = BehaviorCase(
    name="near_duplicate_semantics",
    edge_case="Near-duplicate workflows differ in both a boundary rule and output ordering.",
    baseline_files={
        **_COMMON,
        "app/accounts.py": """def active_trial_names(
    accounts: list[dict[str, object]], day: int
) -> list[str]:
    names: list[str] = []
    for account in accounts:
        expires = account.get("expires")
        name = account.get("name")
        if isinstance(expires, int) and isinstance(name, str) and expires >= day:
            names.append(name)
    return sorted(names)


def active_paid_names(
    accounts: list[dict[str, object]], day: int
) -> list[str]:
    names: list[str] = []
    for account in accounts:
        expires = account.get("expires")
        name = account.get("name")
        if isinstance(expires, int) and isinstance(name, str) and expires > day:
            names.append(name)
    return names
""",
        "tests/test_accounts.py": """from app.accounts import active_paid_names, active_trial_names


ACCOUNTS = [
    {"name": "Ada", "expires": 12},
    {"name": "Lin", "expires": 15},
]


def test_active_names() -> None:
    assert active_trial_names(ACCOUNTS, 10) == ["Ada", "Lin"]
    assert active_paid_names(ACCOUNTS, 10) == ["Ada", "Lin"]
""",
    },
    hidden_tests={
        "tests/oracle/test_account_semantics.py": """from app.accounts import (
    active_paid_names,
    active_trial_names,
)


def test_expiry_boundary_differs_by_account_kind() -> None:
    accounts = [{"name": "Boundary", "expires": 10}]
    assert active_trial_names(accounts, 10) == ["Boundary"]
    assert active_paid_names(accounts, 10) == []


def test_only_trials_are_sorted() -> None:
    accounts = [
        {"name": "Zed", "expires": 20},
        {"name": "Ada", "expires": 20},
    ]
    assert active_trial_names(accounts, 10) == ["Ada", "Zed"]
    assert active_paid_names(accounts, 10) == ["Zed", "Ada"]


def test_malformed_records_are_ignored() -> None:
    accounts = [{"name": 9, "expires": 20}, {"name": "Ada", "expires": "20"}]
    assert active_trial_names(accounts, 10) == []
    assert active_paid_names(accounts, 10) == []
""",
    },
    structural_expectations=(
        StructuralExpectation(
            "The duplicate record filtering loop is extracted once.",
            "app/accounts.py",
            "Both public functions delegate to a shared private helper.",
        ),
        StructuralExpectation(
            "Trial inclusivity and sorting remain explicit policy differences.",
            "app/accounts.py",
            "The helper or its callers represent >= versus > and sorted versus input order.",
        ),
    ),
    trap_edits={
        "app/accounts.py": """def _active_names(
    accounts: list[dict[str, object]], day: int
) -> list[str]:
    names: list[str] = []
    for account in accounts:
        expires = account.get("expires")
        name = account.get("name")
        if isinstance(expires, int) and isinstance(name, str) and expires >= day:
            names.append(name)
    return sorted(names)


def active_trial_names(
    accounts: list[dict[str, object]], day: int
) -> list[str]:
    return _active_names(accounts, day)


def active_paid_names(
    accounts: list[dict[str, object]], day: int
) -> list[str]:
    return _active_names(accounts, day)
""",
    },
)


BEHAVIOR_CASES: tuple[BehaviorCase, ...] = (ROUNDING, GUARD_CLAUSES, NEAR_DUPLICATES)
CASES = BEHAVIOR_CASES
