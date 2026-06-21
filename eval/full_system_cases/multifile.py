"""Multi-file cases for the full-system, generic-prompt benchmark.

The agent only receives ``USER_PROMPT``.  Repository-local architecture notes
and deprecation markers give the audit/planning layer enough evidence to select
a concrete refactor without baking that refactor into the user request.
Hidden tests are deliberately stored separately from the materialized baseline.
"""

# ruff: noqa: E501 -- source fixtures intentionally retain readable one-line mappings.

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

USER_PROMPT = "refactor this codebase"


@dataclass(frozen=True)
class StructuralExpectation:
    """A machine-checkable property of a landed refactor."""

    kind: str
    path: str
    symbol: str
    target: str | None = None


@dataclass(frozen=True)
class MultiFileCase:
    name: str
    rationale: str
    files: dict[str, str]
    hidden_oracle: str
    expectations: tuple[StructuralExpectation, ...]

    @property
    def user_prompt(self) -> str:
        return USER_PROMPT

    @property
    def baseline_files(self) -> dict[str, str]:
        return self.files

    @property
    def hidden_tests(self) -> str:
        return self.hidden_oracle

    @property
    def structural_expectations(self) -> tuple[StructuralExpectation, ...]:
        return self.expectations


RENAME_REEXPORT = MultiFileCase(
    name="rename_with_call_sites_and_reexport",
    rationale="A deprecated internal name has two call sites and a package re-export.",
    files={
        "contact/__init__.py": 'from contact.normalize import clean_email\n\n__all__ = ["clean_email"]\n',
        "contact/normalize.py": '''def clean_email(value: str) -> str:\n    """Deprecated name: repository convention is ``canonicalize_*``."""\n    return value.strip().lower()\n''',
        "contact/profile.py": "from contact.normalize import clean_email\n\n\ndef email_key(raw: str) -> str:\n    return clean_email(raw)\n",
        "contact/importer.py": "import contact.normalize\n\n\ndef import_email(raw: str) -> str:\n    return contact.normalize.clean_email(raw)\n",
        "ARCHITECTURE.md": "Normalizer functions use the canonicalize_* naming convention. Keep documented public imports compatible.\n",
        "tests/test_visible.py": "from contact.profile import email_key\n\n\ndef test_email_key() -> None:\n    assert email_key(' A@EXAMPLE.COM ') == 'a@example.com'\n",
    },
    hidden_oracle="""from contact import clean_email
from contact.importer import import_email
from contact.normalize import canonicalize_email


def test_all_call_paths_and_legacy_export() -> None:
    assert canonicalize_email(" X@EXAMPLE.COM ") == "x@example.com"
    assert import_email(" Y@EXAMPLE.COM ") == "y@example.com"
    assert clean_email(" Z@EXAMPLE.COM ") == "z@example.com"
""",
    expectations=(
        StructuralExpectation("defines", "contact/normalize.py", "canonicalize_email"),
        StructuralExpectation("calls", "contact/profile.py", "canonicalize_email"),
        StructuralExpectation("calls", "contact/importer.py", "canonicalize_email"),
        StructuralExpectation("exports", "contact/__init__.py", "clean_email"),
    ),
)


MOVE_SYMBOL = MultiFileCase(
    name="move_symbol_and_update_imports",
    rationale="A pure presentation helper is misplaced in a domain module and used by two consumers.",
    files={
        "shop/__init__.py": "",
        "shop/billing.py": '''from decimal import Decimal


def format_money(amount: Decimal) -> str:
    """Pure presentation helper; move to shop.presentation per ARCHITECTURE.md."""
    return f"${amount:.2f}"


def subtotal(prices: list[Decimal]) -> Decimal:
    return sum(prices, start=Decimal("0"))
''',
        "shop/presentation.py": 'def format_order_id(value: int) -> str:\n    return f"ORD-{value:06d}"\n',
        "shop/receipt.py": "from shop.billing import format_money, subtotal\n\n\ndef receipt_total(prices):\n    return format_money(subtotal(prices))\n",
        "shop/admin.py": "import shop.billing as billing\n\n\ndef display_credit(amount):\n    return billing.format_money(amount)\n",
        "ARCHITECTURE.md": "Pure formatting helpers belong in shop.presentation. Domain calculations remain in their domain modules.\n",
        "tests/test_visible.py": """from decimal import Decimal
from shop.receipt import receipt_total


def test_receipt_total() -> None:
    assert receipt_total([Decimal("1.20"), Decimal("2.30")]) == "$3.50"
""",
    },
    hidden_oracle="""from decimal import Decimal
from shop.admin import display_credit
from shop.presentation import format_money


def test_moved_helper_and_second_consumer() -> None:
    assert format_money(Decimal("2")) == "$2.00"
    assert display_credit(Decimal("3.456")) == "$3.46"
""",
    expectations=(
        StructuralExpectation("defines", "shop/presentation.py", "format_money"),
        StructuralExpectation("not_defines", "shop/billing.py", "format_money"),
        StructuralExpectation(
            "imports_from", "shop/receipt.py", "format_money", "shop.presentation"
        ),
        StructuralExpectation("imports_from", "shop/admin.py", "format_money", "shop.presentation"),
    ),
)


PUBLIC_API = MultiFileCase(
    name="internal_rename_preserves_public_api",
    rationale="An internal convention change must retain an established package-level API.",
    files={
        "catalog/__init__.py": 'from catalog.slug import make_slug\n\n__all__ = ["make_slug"]\n',
        "catalog/slug.py": '''import re


def make_slug(title: str) -> str:
    """Public API. Internally, builders use the ``build_*`` convention."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
''',
        "catalog/product.py": 'from catalog.slug import make_slug\n\n\ndef product_path(title: str) -> str:\n    return f"/products/{make_slug(title)}"\n',
        "ARCHITECTURE.md": "Internal constructors use build_* names. Symbols exported in package __all__ are compatibility contracts.\n",
        "tests/test_visible.py": "from catalog.product import product_path\n\n\ndef test_product_path() -> None:\n    assert product_path('Red Shirt') == '/products/red-shirt'\n",
    },
    hidden_oracle="""from catalog import make_slug
from catalog.slug import build_slug


def test_new_internal_name_and_legacy_public_api() -> None:
    assert build_slug("Rock & Roll") == "rock-roll"
    assert make_slug("Rock & Roll") == "rock-roll"
""",
    expectations=(
        StructuralExpectation("defines", "catalog/slug.py", "build_slug"),
        StructuralExpectation("calls", "catalog/product.py", "build_slug"),
        StructuralExpectation("exports", "catalog/__init__.py", "make_slug"),
    ),
)


CASES = (RENAME_REEXPORT, MOVE_SYMBOL, PUBLIC_API)


def materialize(case: MultiFileCase, destination: Path) -> Path:
    """Write only agent-visible baseline files."""

    for relative, content in case.files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return destination


def structural_failures(case: MultiFileCase, root: Path) -> list[str]:
    """Evaluate structural expectations without executing candidate code."""

    failures: list[str] = []
    for expectation in case.expectations:
        path = root / expectation.path
        if not path.is_file():
            failures.append(f"missing {expectation.path}")
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            failures.append(f"invalid Python in {expectation.path}")
            continue
        if not _matches(tree, expectation):
            failures.append(f"{expectation.kind} {expectation.symbol} in {expectation.path}")
    return failures


def _matches(tree: ast.AST, expectation: StructuralExpectation) -> bool:
    definitions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    if expectation.kind == "defines":
        return expectation.symbol in definitions
    if expectation.kind == "not_defines":
        return expectation.symbol not in definitions
    if expectation.kind == "calls":
        return any(
            _call_name(node.func) == expectation.symbol
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        )
    if expectation.kind == "imports_from":
        return any(
            isinstance(node, ast.ImportFrom)
            and node.module == expectation.target
            and any(alias.name == expectation.symbol for alias in node.names)
            for node in ast.walk(tree)
        )
    if expectation.kind == "exports":
        return any(
            (
                isinstance(node, ast.ImportFrom)
                and any(alias.name == expectation.symbol for alias in node.names)
            )
            or (isinstance(node, ast.Assign) and _assigned_string(node, expectation.symbol))
            for node in ast.walk(tree)
        )
    raise ValueError(f"unknown structural expectation: {expectation.kind}")


def _call_name(function: ast.expr) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _assigned_string(node: ast.Assign, symbol: str) -> bool:
    return any(
        isinstance(item, ast.Constant) and item.value == symbol for item in ast.walk(node.value)
    )
