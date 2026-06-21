"""Recovery-focused full-system benchmark cases.

Each case exposes only a generic user request and a repository.  Candidate
attempts are deterministic calibration controls for the verification/recovery
path; they are not prompts shown to an agent.  Oracle tests remain separate
until final grading.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FailureClass = Literal["behavior-regression", "static-analysis", "retry-exhausted"]


@dataclass(frozen=True)
class RecoveryCase:
    name: str
    baseline: dict[str, str]
    attempts: tuple[dict[str, str], ...]
    hidden_oracle: str
    expected_gate: Literal["parse", "lint", "typecheck", "tests"]
    expected_failure: FailureClass
    expected_diagnostics: tuple[str, ...]
    max_retries: int = 2
    initial_prompt: str = "refactor this codebase"

    @property
    def user_prompt(self) -> str:
        return self.initial_prompt

    @property
    def baseline_files(self) -> dict[str, str]:
        """Compatibility name used by the full-system case runner."""

        return self.baseline

    @property
    def hidden_tests(self) -> str:
        """Tests withheld from both agent arms until oracle grading."""

        return self.hidden_oracle

    @property
    def structural_expectations(self) -> dict[str, object]:
        return {
            "failure_class": self.expected_failure,
            "failure_gate": self.expected_gate,
            "diagnostic_needles": self.expected_diagnostics,
            "atomic_rollback": True,
        }


_STRICT_CONFIG = '{"include":["app"],"typeCheckingMode":"strict"}\n'


def _behavior_regression() -> RecoveryCase:
    baseline = {
        "app/__init__.py": "",
        "app/pricing.py": (
            "def shipping_total(subtotal: int) -> int:\n"
            "    fee = 0 if subtotal >= 50 else 5\n"
            "    return subtotal + fee\n"
        ),
        "app/checkout.py": (
            "from app.pricing import shipping_total\n\n\n"
            "def checkout_total(subtotal: int) -> int:\n"
            "    return shipping_total(subtotal)\n"
        ),
        "tests/gate/test_checkout.py": (
            "from app.checkout import checkout_total\n\n\n"
            "def test_free_shipping_threshold() -> None:\n"
            "    assert checkout_total(50) == 50\n"
        ),
        "pyrightconfig.json": _STRICT_CONFIG,
    }
    attempt = {
        "app/pricing.py": (
            "FREE_SHIPPING_MINIMUM = 50\n\n\n"
            "def shipping_fee(subtotal: int) -> int:\n"
            "    return 0 if subtotal > FREE_SHIPPING_MINIMUM else 5\n\n\n"
            "def shipping_total(subtotal: int) -> int:\n"
            "    return subtotal + shipping_fee(subtotal)\n"
        ),
        "app/checkout.py": (
            "from app.pricing import shipping_total\n\n\n"
            "def checkout_total(subtotal: int) -> int:\n"
            "    return shipping_total(subtotal)\n"
        ),
    }
    oracle = (
        "from app.checkout import checkout_total\n\n\n"
        "def test_values_around_threshold() -> None:\n"
        "    assert checkout_total(49) == 54\n"
        "    assert checkout_total(50) == 50\n"
        "    assert checkout_total(51) == 51\n"
    )
    return RecoveryCase(
        name="type_clean_threshold_regression",
        baseline=baseline,
        attempts=(attempt,),
        hidden_oracle=oracle,
        expected_gate="tests",
        expected_failure="behavior-regression",
        expected_diagnostics=("test_free_shipping_threshold", "50"),
    )


def _type_failure() -> RecoveryCase:
    baseline = {
        "app/__init__.py": "",
        "app/inventory.py": (
            "def available(stock: int, reserved: int) -> int:\n"
            "    return max(stock - reserved, 0)\n"
        ),
        "app/catalog.py": (
            "from app.inventory import available\n\n\n"
            "def units_for_sale(stock: int, reserved: int) -> int:\n"
            "    return available(stock, reserved)\n"
        ),
        "tests/gate/test_catalog.py": (
            "from app.catalog import units_for_sale\n\n\n"
            "def test_available_units() -> None:\n"
            "    assert units_for_sale(8, 3) == 5\n"
        ),
        "pyrightconfig.json": _STRICT_CONFIG,
    }
    attempt = {
        "app/inventory.py": (
            "def available(stock: int, reserved: int) -> int | None:\n"
            "    remaining = stock - reserved\n"
            "    return remaining if remaining >= 0 else None\n"
        ),
        "app/catalog.py": (
            "from app.inventory import available\n\n\n"
            "def units_for_sale(stock: int, reserved: int) -> int:\n"
            "    return available(stock, reserved)\n"
        ),
    }
    oracle = (
        "from app.catalog import units_for_sale\n\n\n"
        "def test_reservations_cannot_make_inventory_negative() -> None:\n"
        "    assert units_for_sale(2, 5) == 0\n"
    )
    return RecoveryCase(
        name="nullable_return_requires_targeted_repair",
        baseline=baseline,
        attempts=(attempt,),
        hidden_oracle=oracle,
        expected_gate="typecheck",
        expected_failure="static-analysis",
        expected_diagnostics=(
            "app/catalog.py",
            'Type "int | None" is not assignable to return type "int"',
        ),
    )


def _retry_exhaustion() -> RecoveryCase:
    baseline = {
        "app/__init__.py": "",
        "app/normalize.py": (
            "def normalized_name(value: str) -> str:\n"
            "    return value.strip().casefold()\n"
        ),
        "app/profile.py": (
            "from app.normalize import normalized_name\n\n\n"
            "def profile_key(name: str) -> str:\n"
            "    return f\"user:{normalized_name(name)}\"\n"
        ),
        "tests/gate/test_profile.py": (
            "from app.profile import profile_key\n\n\n"
            "def test_profile_key() -> None:\n"
            "    assert profile_key(\" Ada \" ) == \"user:ada\"\n"
        ),
        "pyrightconfig.json": _STRICT_CONFIG,
    }
    broken_attempts = tuple(
        {
            "app/normalize.py": (
                f"DEFAULT_PREFIX = \"attempt-{attempt}\"\n\n\n"
                "def normalized_name(value: str) -> str:\n"
                "    return value.strip().casefold()\n"
            ),
            "app/profile.py": (
                "from app.normalize import normalized_name\n\n\n"
                "def profile_key(name: str) -> str\n"
                "    return f\"user:{normalized_name(name)}\"\n"
            ),
        }
        for attempt in range(3)
    )
    oracle = (
        "from app.profile import profile_key\n\n\n"
        "def test_unicode_and_whitespace_are_preserved_semantically() -> None:\n"
        "    assert profile_key(\"  STRASSE  \" ) == \"user:strasse\"\n"
    )
    return RecoveryCase(
        name="repeated_invalid_repairs_escalate",
        baseline=baseline,
        attempts=broken_attempts,
        hidden_oracle=oracle,
        expected_gate="parse",
        expected_failure="retry-exhausted",
        expected_diagnostics=("app/profile.py:4", "expected ':'"),
    )


RECOVERY_CASES: tuple[RecoveryCase, ...] = (
    _behavior_regression(),
    _type_failure(),
    _retry_exhaustion(),
)

# Uniform discovery interface shared by the full-system case modules.
CASES = RECOVERY_CASES


def materialize(case: RecoveryCase, destination: Path) -> Path:
    """Write only agent-visible baseline files into ``destination``."""

    destination.mkdir(parents=True, exist_ok=True)
    for relative, content in case.baseline.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return destination
