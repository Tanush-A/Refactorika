"""Additional contract-heavy fixtures for the full-system stress benchmark."""

# ruff: noqa: E501 -- embedded repository fixtures are kept legible as source strings.

from __future__ import annotations

from .stress import StressCase, StressExpectation

_BASE = {"app/__init__.py": "", "tests/__init__.py": ""}


ALIAS_AND_QUALIFIED = StressCase(
    name="extra_alias_and_qualified_imports",
    category="dependency-topology",
    rationale="A rename must follow aliases and qualified calls while retaining compatibility.",
    baseline_files={
        **_BASE,
        "app/ids.py": '''def make_id(raw: str) -> str:
    """Deprecated internal name; ID constructors use build_* names."""
    return raw.strip().casefold().replace(" ", "-")
''',
        "app/direct.py": "from app.ids import make_id as identifier\n\n\ndef direct(raw: str) -> str:\n    return identifier(raw)\n",
        "app/qualified.py": "import app.ids\n\n\ndef qualified(raw: str) -> str:\n    return app.ids.make_id(raw)\n",
        "ARCHITECTURE.md": "ID constructors use build_* internally; legacy imports remain supported.\n",
        "tests/test_visible.py": "from app.direct import direct\n\n\ndef test_direct() -> None:\n    assert direct(' A B ') == 'a-b'\n",
    },
    hidden_tests='''from app.ids import make_id
from app.qualified import qualified


def test_qualified_and_compatibility_paths() -> None:
    assert qualified(" C D ") == "c-d"
    assert make_id(" E F ") == "e-f"
''',
    expectations=(
        StressExpectation("defines", "app/ids.py", "build_id"),
        StressExpectation("imports_from", "app/direct.py", "build_id", "app.ids"),
        StressExpectation("calls", "app/qualified.py", "build_id"),
        StressExpectation("defines", "app/ids.py", "make_id"),
    ),
)


CIRCULAR_MOVE = StressCase(
    name="extra_circular_import_sensitive_move",
    category="dependency-topology",
    rationale="Moving a formatter into a module that imports the model can introduce a cycle.",
    baseline_files={
        **_BASE,
        "app/model.py": '''from dataclasses import dataclass


@dataclass(frozen=True)
class Invoice:
    number: int


def format_invoice(invoice: Invoice) -> str:
    """Presentation helpers belong in app.presentation."""
    return f"INV-{invoice.number:04d}"
''',
        "app/presentation.py": "from app.model import Invoice\n\n\ndef invoice_heading(invoice: Invoice) -> str:\n    return f\"Invoice {invoice.number}\"\n",
        "app/email.py": "from app.model import Invoice, format_invoice\n\n\ndef subject(invoice: Invoice) -> str:\n    return format_invoice(invoice)\n",
        "ARCHITECTURE.md": "Presentation helpers live in app.presentation. Avoid runtime model/presentation import cycles; use forward annotations when needed.\n",
        "tests/test_visible.py": "from app.email import subject\nfrom app.model import Invoice\n\n\ndef test_subject() -> None:\n    assert subject(Invoice(7)) == 'INV-0007'\n",
    },
    hidden_tests='''from app.model import Invoice, format_invoice
from app.presentation import invoice_heading


def test_module_import_and_both_formatters() -> None:
    invoice = Invoice(12)
    assert format_invoice(invoice) == "INV-0012"
    assert invoice_heading(invoice) == "Invoice 12"
''',
    expectations=(
        StressExpectation("defines", "app/presentation.py", "format_invoice"),
        StressExpectation("not_defines", "app/model.py", "format_invoice"),
        StressExpectation("imports_from", "app/email.py", "format_invoice", "app.presentation"),
    ),
)


PACKAGE_EXPORT = StressCase(
    name="extra_package_export_contract",
    category="public-api",
    rationale="An internal rename must not remove a package-level exported symbol.",
    baseline_files={
        **_BASE,
        "app/__init__.py": "from app.token import make_token\n\n__all__ = ['make_token']\n",
        "app/token.py": "def make_token(value: int) -> str:\n    return f'tok-{value}'\n",
        "app/session.py": "from app.token import make_token\n\n\ndef session_key(value: int) -> str:\n    return make_token(value)\n",
        "ARCHITECTURE.md": "Internal factories use build_* names. app.__all__ is a stable public contract.\n",
        "tests/test_visible.py": "from app.session import session_key\n\n\ndef test_key() -> None:\n    assert session_key(3) == 'tok-3'\n",
    },
    hidden_tests='''from app import make_token


def test_package_export() -> None:
    assert make_token(5) == "tok-5"
''',
    expectations=(
        StressExpectation("defines", "app/token.py", "build_token"),
        StressExpectation("calls", "app/session.py", "build_token"),
        StressExpectation("exports", "app/__init__.py", "make_token"),
    ),
)


KEYWORD_SIGNATURE = StressCase(
    name="extra_keyword_signature_compatibility",
    category="public-api",
    rationale="Internal cleanup must retain legacy parameter names used as keywords.",
    baseline_files={
        **_BASE,
        "app/render.py": '''def make_label(text: str, upper: bool = False) -> str:
    """Public wrapper; internal constructors use build_* names."""
    clean = text.strip()
    return clean.upper() if upper else clean
''',
        "app/menu.py": "from app.render import make_label\n\n\ndef menu_label(name: str) -> str:\n    return make_label(text=name, upper=True)\n",
        "ARCHITECTURE.md": "Builders use build_* internally. Public keyword names and defaults are stable.\n",
        "tests/test_visible.py": "from app.menu import menu_label\n\n\ndef test_menu() -> None:\n    assert menu_label(' home ') == 'HOME'\n",
    },
    hidden_tests='''import inspect

from app.render import make_label


def test_legacy_keywords_and_signature() -> None:
    assert make_label(text=" Fine ", upper=False) == "Fine"
    signature = inspect.signature(make_label)
    assert list(signature.parameters) == ["text", "upper"]
    assert signature.parameters["upper"].default is False
''',
    expectations=(
        StressExpectation("defines", "app/render.py", "build_label"),
        StressExpectation("calls", "app/menu.py", "build_label"),
        StressExpectation("defines", "app/render.py", "make_label"),
    ),
)


DYNAMIC_PLUGIN = StressCase(
    name="extra_dynamic_plugin_path",
    category="runtime-discovery",
    rationale="A helper move must leave the dynamically imported plugin class at its registered path.",
    baseline_files={
        **_BASE,
        "app/plugins/__init__.py": "",
        "app/plugins/csv.py": '''class Plugin:
    def run(self, row: str) -> list[str]:
        return [cell.strip() for cell in row.split(",")]


def preview(row: str) -> str:
    return " | ".join(cell.strip() for cell in row.split(","))
''',
        "app/plugin_utils.py": "",
        "app/loader.py": '''from importlib import import_module


def load(path: str):
    module_name, class_name = path.rsplit(".", 1)
    return getattr(import_module(module_name), class_name)()
''',
        "ARCHITECTURE.md": "Shared plugin parsing belongs in app.plugin_utils. Registered class path app.plugins.csv.Plugin is stable.\n",
        "tests/test_visible.py": "from app.plugins.csv import preview\n\n\ndef test_preview() -> None:\n    assert preview(' a, b ') == 'a | b'\n",
    },
    hidden_tests='''from app.loader import load


def test_dynamic_registration_path() -> None:
    plugin = load("app.plugins.csv.Plugin")
    assert plugin.run(" x, y ") == ["x", "y"]
''',
    expectations=(
        StressExpectation("defines", "app/plugin_utils.py", "parse_row"),
        StressExpectation("calls", "app/plugins/csv.py", "parse_row"),
    ),
)


DATACLASS_CONTRACT = StressCase(
    name="extra_dataclass_contract",
    category="data-model",
    rationale="Deduplication must preserve dataclass field order, defaults, and equality.",
    baseline_files={
        **_BASE,
        "app/events.py": '''from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    name: str
    retries: int = 0


def event_key(event: Event) -> str:
    return f"{event.name.strip().casefold()}:{event.retries}"


def event_label(event: Event) -> str:
    return f"{event.name.strip().casefold()} ({event.retries})"
''',
        "ARCHITECTURE.md": "Repeated Event name normalization belongs in one private helper. Dataclass shape is public.\n",
        "tests/test_visible.py": "from app.events import Event, event_key\n\n\ndef test_key() -> None:\n    assert event_key(Event(' Deploy ', 2)) == 'deploy:2'\n",
    },
    hidden_tests='''from dataclasses import fields

from app.events import Event, event_label


def test_dataclass_shape_and_value_semantics() -> None:
    assert [field.name for field in fields(Event)] == ["name", "retries"]
    assert Event("x") == Event(name="x", retries=0)
    assert event_label(Event(" Deploy ", 1)) == "deploy (1)"
''',
    expectations=(
        StressExpectation("calls_private", "app/events.py", "event_key"),
        StressExpectation("calls_private", "app/events.py", "event_label"),
    ),
)


PROTOCOL_CALL_SITES = StressCase(
    name="extra_protocol_call_sites",
    category="typing-contract",
    rationale="A concrete rename must preserve the method required by structural protocol users.",
    baseline_files={
        **_BASE,
        "app/senders.py": '''from typing import Protocol


class Sender(Protocol):
    def send(self, message: str) -> str: ...


class EmailSender:
    def send(self, message: str) -> str:
        """Compatibility method; internal delivery methods use deliver_* names."""
        return f"email:{message}"
''',
        "app/notify.py": "from app.senders import Sender\n\n\ndef notify(sender: Sender, text: str) -> str:\n    return sender.send(text)\n",
        "ARCHITECTURE.md": "Concrete delivery implementations use deliver_* internally. Sender.send is a stable protocol.\n",
        "tests/test_visible.py": "from app.notify import notify\nfrom app.senders import EmailSender\n\n\ndef test_notify() -> None:\n    assert notify(EmailSender(), 'hi') == 'email:hi'\n",
    },
    hidden_tests='''from app.notify import notify
from app.senders import EmailSender


class TestSender:
    def send(self, message: str) -> str:
        return f"test:{message}"


def test_structural_protocol_and_concrete_compatibility() -> None:
    assert notify(TestSender(), "x") == "test:x"
    assert EmailSender().send("y") == "email:y"
''',
    expectations=(
        StressExpectation("defines", "app/senders.py", "deliver_email"),
        StressExpectation("calls", "app/senders.py", "deliver_email"),
        StressExpectation("defines", "app/senders.py", "send"),
    ),
)


GENERATED_EXCLUSION = StressCase(
    name="extra_generated_vendor_exclusion",
    category="discovery-scope",
    rationale="A checked-in generated twin is evidence, not an authorized edit target.",
    baseline_files={
        **_BASE,
        "app/colors.py": "def make_color(value: str) -> str:\n    return value.strip().casefold()\n",
        "app/theme.py": "from app.colors import make_color\n\n\ndef theme_color(value: str) -> str:\n    return make_color(value)\n",
        "generated/colors.py": "# GENERATED - DO NOT EDIT\ndef make_color(value: str) -> str:\n    return value.strip().casefold()\n",
        "ARCHITECTURE.md": "Factories use build_* names. Never edit generated/. Keep legacy application APIs compatible.\n",
        "tests/test_visible.py": "from app.theme import theme_color\n\n\ndef test_theme() -> None:\n    assert theme_color(' BLUE ') == 'blue'\n",
    },
    hidden_tests='''from app.colors import make_color
from generated.colors import make_color as generated_make_color


def test_compatibility_and_generated_copy() -> None:
    assert make_color(" RED ") == "red"
    assert generated_make_color(" GREEN ") == "green"
''',
    expectations=(
        StressExpectation("defines", "app/colors.py", "build_color"),
        StressExpectation("calls", "app/theme.py", "build_color"),
        StressExpectation("unchanged", "generated/colors.py"),
    ),
)


DECOY_SYMBOL = StressCase(
    name="extra_decoy_same_named_symbol",
    category="symbol-resolution",
    rationale="A same-named function in another domain must not be included in the rename.",
    baseline_files={
        **_BASE,
        "app/orders.py": "def make_code(number: int) -> str:\n    return f'order-{number}'\n",
        "app/coupons.py": "def make_code(percent: int) -> str:\n    return f'{percent}-off'\n",
        "app/checkout.py": "from app.orders import make_code\n\n\ndef receipt_code(number: int) -> str:\n    return make_code(number)\n",
        "ARCHITECTURE.md": "Order identifier constructors use build_* names. Coupon marketing APIs are unrelated and stable.\n",
        "tests/test_visible.py": "from app.checkout import receipt_code\n\n\ndef test_receipt() -> None:\n    assert receipt_code(8) == 'order-8'\n",
    },
    hidden_tests='''from app.coupons import make_code as coupon_code
from app.orders import make_code as legacy_order_code


def test_decoy_and_legacy_order_api() -> None:
    assert coupon_code(20) == "20-off"
    assert legacy_order_code(9) == "order-9"
''',
    expectations=(
        StressExpectation("defines", "app/orders.py", "build_code"),
        StressExpectation("calls", "app/checkout.py", "build_code"),
        StressExpectation("unchanged", "app/coupons.py"),
    ),
)


ENUM_CONTRACT = StressCase(
    name="extra_enum_value_contract",
    category="data-model",
    rationale="A display refactor must preserve serialized Enum values and member identity.",
    baseline_files={
        **_BASE,
        "app/status.py": '''from enum import Enum


class Status(str, Enum):
    READY = "ready"
    FAILED = "failed"


def status_label(status: Status) -> str:
    return status.value.replace("_", " ").title()


def status_message(status: Status) -> str:
    return f"Status: {status.value.replace('_', ' ').title()}"
''',
        "ARCHITECTURE.md": "Repeated Status display policy belongs in one private helper. Enum names and values are serialized contracts.\n",
        "tests/test_visible.py": "from app.status import Status, status_label\n\n\ndef test_label() -> None:\n    assert status_label(Status.READY) == 'Ready'\n",
    },
    hidden_tests='''from app.status import Status, status_message


def test_enum_serialization_contract() -> None:
    assert Status.READY.value == "ready"
    assert Status("failed") is Status.FAILED
    assert status_message(Status.FAILED) == "Status: Failed"
''',
    expectations=(
        StressExpectation("calls_private", "app/status.py", "status_label"),
        StressExpectation("calls_private", "app/status.py", "status_message"),
    ),
)


STRESS_CASES: tuple[StressCase, ...] = (
    ALIAS_AND_QUALIFIED,
    CIRCULAR_MOVE,
    PACKAGE_EXPORT,
    KEYWORD_SIGNATURE,
    DYNAMIC_PLUGIN,
    DATACLASS_CONTRACT,
    PROTOCOL_CALL_SITES,
    GENERATED_EXCLUSION,
    DECOY_SYMBOL,
    ENUM_CONTRACT,
)
