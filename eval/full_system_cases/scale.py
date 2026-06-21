"""Deterministic medium- and large-repository refactoring fixtures."""

# ruff: noqa: E501 -- source fixtures intentionally retain readable inline mappings.

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .stress import StressCase, StressExpectation


@dataclass(frozen=True)
class ScaleCase(StressCase):
    """A stress case with explicit repository-size metadata."""

    size_tier: str = ""
    source_file_count: int = 0
    source_loc: int = 0
    relevant_file_count: int = 0
    distractor_file_count: int = 0
    fixture_hash: str = ""

    @property
    def benchmark_metadata(self) -> dict[str, object]:
        return {
            "size_tier": self.size_tier,
            "source_file_count": self.source_file_count,
            "source_loc": self.source_loc,
            "relevant_file_count": self.relevant_file_count,
            "distractor_file_count": self.distractor_file_count,
            "fixture_hash": self.fixture_hash,
        }


_CORE_PATHS = (
    "app/__init__.py",
    "app/legacy/__init__.py",
    "app/legacy/phone.py",
    "app/shared/__init__.py",
    "app/shared/phone.py",
    "app/consumers/__init__.py",
    "app/consumers/direct.py",
    "app/consumers/aliased.py",
    "app/consumers/qualified.py",
    "app/consumers/keyword.py",
    "app/public.py",
    "app/registry.py",
)
_PROTECTED_PATH = "vendor/generated_phone.py"


def _legacy_phone() -> str:
    return '''def clean_phone(value: str, keep_extension: bool = False) -> str:
    """Normalize a phone number while retaining the historical API."""
    main, marker, extension = value.partition("x")
    digits = "".join(character for character in main if character.isdigit())
    if keep_extension and marker:
        extension_digits = "".join(character for character in extension if character.isdigit())
        if extension_digits:
            return f"{digits}x{extension_digits}"
    return digits
'''


def _core_files() -> dict[str, str]:
    return {
        "app/__init__.py": '"""Order-processing application."""\n',
        "app/legacy/__init__.py": "from app.legacy.phone import clean_phone\n\n__all__ = [\"clean_phone\"]\n",
        "app/legacy/phone.py": _legacy_phone(),
        "app/shared/__init__.py": '"""Shared domain policies."""\n',
        "app/shared/phone.py": '''def display_extension(extension: str) -> str:
    digits = "".join(character for character in extension if character.isdigit())
    return f"ext. {digits}" if digits else ""
''',
        "app/consumers/__init__.py": '"""Phone-normalization consumers."""\n',
        "app/consumers/direct.py": "from app.legacy.phone import clean_phone\n\n\ndef customer_key(value: str) -> str:\n    return clean_phone(value)\n",
        "app/consumers/aliased.py": "from app.legacy.phone import clean_phone as normalize\n\n\ndef supplier_key(value: str) -> str:\n    return normalize(value)\n",
        "app/consumers/qualified.py": "import app.legacy.phone\n\n\ndef courier_key(value: str) -> str:\n    return app.legacy.phone.clean_phone(value)\n",
        "app/consumers/keyword.py": "from app.legacy.phone import clean_phone\n\n\ndef support_key(value: str) -> str:\n    return clean_phone(value=value, keep_extension=True)\n",
        "app/public.py": "from app.legacy.phone import clean_phone\n\n__all__ = [\"clean_phone\"]\n",
        "app/registry.py": '''from collections.abc import Callable

from app.legacy.phone import clean_phone

NORMALIZERS: dict[str, Callable[[str], str]] = {"phone": clean_phone}


def normalize_registered(kind: str, value: str) -> str:
    return NORMALIZERS[kind](value)
''',
        _PROTECTED_PATH: "# GENERATED FILE - DO NOT EDIT\n" + _legacy_phone(),
    }


def _decoy_module(index: int, helper_count: int) -> str:
    domain = ("billing", "catalog", "fulfillment", "identity", "reporting")[index % 5]
    lines = [
        f'"""{domain.title()} rules for partition {index:03d}."""',
        "",
        "from __future__ import annotations",
        "",
        f"DEFAULT_WINDOW = {index % 7 + 2}",
        "",
        "",
        "def normalize_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:",
        "    result: list[dict[str, object]] = []",
        "    for row in rows:",
        "        if row.get(\"enabled\") is False:",
        "            continue",
        "        copied = dict(row)",
        f"        copied.setdefault(\"domain\", \"{domain}\")",
        "        result.append(copied)",
        "    return result",
    ]
    for helper in range(helper_count):
        offset = (index + helper) % 11
        lines.extend(
            [
                "",
                "",
                f"def score_{helper:02d}(value: int, weight: int = DEFAULT_WINDOW) -> int:",
                f"    adjusted = value + {offset}",
                "    if adjusted < 0:",
                "        return 0",
                "    return adjusted * weight",
            ]
        )
    lines.extend(
        [
            "",
            "",
            "def summarize(rows: list[dict[str, object]]) -> tuple[int, int]:",
            "    normalized = normalize_rows(rows)",
            "    total = sum(int(row.get(\"amount\", 0)) for row in normalized)",
            "    return len(normalized), total",
            "",
        ]
    )
    return "\n".join(lines)


def _visible_tests() -> str:
    return '''from app.consumers.direct import customer_key
from app.registry import normalize_registered


def test_visible_phone_paths() -> None:
    assert customer_key("(415) 555-0100") == "4155550100"
    assert normalize_registered("phone", "03 12") == "0312"
'''


_HIDDEN_TESTS = '''import inspect

from app.consumers.aliased import supplier_key
from app.consumers.keyword import support_key
from app.consumers.qualified import courier_key
from app.legacy.phone import clean_phone
from app.public import clean_phone as public_clean_phone
from app.registry import NORMALIZERS, normalize_registered
import app.shared.phone as shared_phone

canonicalize_phone = getattr(shared_phone, "canonicalize_phone", clean_phone)


def test_all_caller_shapes_use_the_canonical_policy() -> None:
    assert supplier_key("+1 212") == "1212"
    assert courier_key("0044 20") == "004420"
    assert support_key("03 12 x 009") == "0312x009"
    assert normalize_registered("phone", "001-90") == "00190"
    assert NORMALIZERS["phone"] is canonicalize_phone


def test_legacy_api_and_signature_remain_compatible() -> None:
    assert public_clean_phone is clean_phone
    assert clean_phone(value="007 01", keep_extension=True) == "00701"
    assert list(inspect.signature(clean_phone).parameters) == ["value", "keep_extension"]


def test_input_and_boundary_semantics() -> None:
    value = " 000-42 x 007 "
    before = value
    assert canonicalize_phone(value, keep_extension=True) == "00042x007"
    assert canonicalize_phone("x 12", keep_extension=True) == "x12"
    assert value == before
'''


def _fixture_hash(files: dict[str, str]) -> str:
    digest = sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(content.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_paths(files: dict[str, str]) -> list[str]:
    return sorted(path for path in files if path.endswith(".py") and not path.startswith("tests/"))


def build_scale_case(source_count: int) -> ScaleCase:
    """Build one reproducible repository with a fixed production-file count."""

    if source_count not in {20, 100}:
        raise ValueError("scale cases support exactly 20 or 100 production files")
    files = _core_files()
    decoy_count = source_count - len(_source_paths(files))
    helper_count = 22 if source_count == 20 else 8
    for index in range(decoy_count):
        files[f"app/domains/{index:03d}_rules.py"] = _decoy_module(index, helper_count)
    files.update(
        {
            "tests/__init__.py": "",
            "tests/test_phone.py": _visible_tests(),
            "ARCHITECTURE.md": '''# Architecture

Shared normalization policies belong in `app/shared`. Legacy public APIs remain
as compatibility wrappers, while application callers and registries use the
canonical implementation directly. Complete moves atomically across every
import style. Never modify generated or vendor files. Unrelated domain modules
are outside the scope of a normalization refactor.
''',
            "pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"-q\"\n",
        }
    )
    source_paths = _source_paths(files)
    source_loc = sum(len(files[path].splitlines()) for path in source_paths)
    tier = "medium" if source_count == 20 else "large"
    return ScaleCase(
        name=f"scale_{source_count}_file_rename_move",
        category="repository-scale",
        rationale="A high-fanout legacy normalizer must move while preserving compatibility and boundaries.",
        baseline_files=files,
        hidden_tests=_HIDDEN_TESTS,
        expectations=(
            StressExpectation("defines", "app/shared/phone.py", "canonicalize_phone"),
            StressExpectation("defines", "app/legacy/phone.py", "clean_phone"),
            StressExpectation("calls", "app/legacy/phone.py", "canonicalize_phone"),
            StressExpectation("calls", "app/consumers/direct.py", "canonicalize_phone"),
            StressExpectation("imports_from", "app/consumers/aliased.py", "canonicalize_phone", "app.shared.phone"),
            StressExpectation("calls", "app/consumers/qualified.py", "canonicalize_phone"),
            StressExpectation("calls", "app/consumers/keyword.py", "canonicalize_phone"),
            StressExpectation("exports", "app/public.py", "clean_phone"),
            StressExpectation("imports_from", "app/registry.py", "canonicalize_phone", "app.shared.phone"),
            StressExpectation("unchanged", _PROTECTED_PATH),
        ),
        size_tier=tier,
        source_file_count=source_count,
        source_loc=source_loc,
        relevant_file_count=len(_CORE_PATHS),
        distractor_file_count=source_count - len(_CORE_PATHS),
        fixture_hash=_fixture_hash(files),
    )


def reference_edits(case: ScaleCase) -> dict[str, str]:
    """Return the minimal complete patch used to calibrate the scale oracle."""

    canonical = '''def canonicalize_phone(value: str, keep_extension: bool = False) -> str:
    """Return stable digits and, when requested, a normalized extension."""
    main, marker, extension = value.partition("x")
    digits = "".join(character for character in main if character.isdigit())
    if keep_extension and marker:
        extension_digits = "".join(character for character in extension if character.isdigit())
        if extension_digits:
            return f"{digits}x{extension_digits}"
    return digits


def display_extension(extension: str) -> str:
    digits = "".join(character for character in extension if character.isdigit())
    return f"ext. {digits}" if digits else ""
'''
    return {
        "app/shared/phone.py": canonical,
        "app/legacy/phone.py": '''from app.shared.phone import canonicalize_phone


def clean_phone(value: str, keep_extension: bool = False) -> str:
    """Compatibility wrapper for the historical public API."""
    return canonicalize_phone(value, keep_extension=keep_extension)
''',
        "app/consumers/direct.py": "from app.shared.phone import canonicalize_phone\n\n\ndef customer_key(value: str) -> str:\n    return canonicalize_phone(value)\n",
        "app/consumers/aliased.py": "from app.shared.phone import canonicalize_phone as normalize\n\n\ndef supplier_key(value: str) -> str:\n    return normalize(value)\n",
        "app/consumers/qualified.py": "import app.shared.phone\n\n\ndef courier_key(value: str) -> str:\n    return app.shared.phone.canonicalize_phone(value)\n",
        "app/consumers/keyword.py": "from app.shared.phone import canonicalize_phone\n\n\ndef support_key(value: str) -> str:\n    return canonicalize_phone(value=value, keep_extension=True)\n",
        "app/registry.py": '''from collections.abc import Callable

from app.shared.phone import canonicalize_phone

NORMALIZERS: dict[str, Callable[[str], str]] = {"phone": canonicalize_phone}


def normalize_registered(kind: str, value: str) -> str:
    return NORMALIZERS[kind](value)
''',
    }


def bad_control_edits(case: ScaleCase) -> dict[str, dict[str, str]]:
    """Known-invalid variants that prove the oracle catches realistic failures."""

    good = reference_edits(case)
    missed_caller = dict(good)
    missed_caller["app/consumers/aliased.py"] = case.baseline_files["app/consumers/aliased.py"]
    broken_legacy = dict(good)
    broken_legacy["app/legacy/phone.py"] = "from app.shared.phone import canonicalize_phone as clean_phone\n"
    boundary = dict(good)
    boundary["app/shared/phone.py"] = good["app/shared/phone.py"].replace(
        'digits = "".join(character for character in main if character.isdigit())',
        'digits = str(int("".join(character for character in main if character.isdigit()) or "0"))',
    )
    protected = dict(good)
    protected[_PROTECTED_PATH] = case.baseline_files[_PROTECTED_PATH] + "\n# hand edited\n"
    return {
        "missed_alias_caller": missed_caller,
        "broken_legacy_contract": broken_legacy,
        "leading_zero_boundary": boundary,
        "protected_vendor_edit": protected,
    }


SCALE_CASES: tuple[ScaleCase, ...] = (build_scale_case(20), build_scale_case(100))
CASES = SCALE_CASES
