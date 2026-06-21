"""Higher-difficulty, declaratively graded full-system refactoring cases."""

# ruff: noqa: E501 -- source fixtures intentionally retain readable inline mappings.

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

USER_PROMPT = "refactor this codebase"

ExpectationKind = Literal[
    "calls",
    "calls_private",
    "defines",
    "exports",
    "imports_from",
    "not_defines",
    "raise_from",
    "unchanged",
]


@dataclass(frozen=True)
class StressExpectation:
    kind: ExpectationKind
    path: str
    symbol: str = ""
    target: str | None = None


@dataclass(frozen=True)
class StressCase:
    name: str
    category: str
    rationale: str
    baseline_files: dict[str, str]
    hidden_tests: str
    expectations: tuple[StressExpectation, ...]
    difficulty: Literal["medium", "hard"] = "hard"
    user_prompt: str = USER_PROMPT

    @property
    def structural_expectations(self) -> tuple[StressExpectation, ...]:
        return self.expectations


_INIT = {"app/__init__.py": "", "tests/__init__.py": ""}


ALIASED_RENAME = StressCase(
    name="aliased_qualified_multifile_rename",
    category="dependency-topology",
    rationale="A deprecated symbol is reached through direct, aliased, and qualified imports.",
    baseline_files={
        **_INIT,
        "app/phone.py": '''def clean_phone(value: str) -> str:
    """Deprecated: normalizers use the canonicalize_* convention."""
    return "".join(character for character in value if character.isdigit())
''',
        "app/direct.py": "from app.phone import clean_phone\n\n\ndef direct_key(value: str) -> str:\n    return clean_phone(value)\n",
        "app/aliased.py": "from app.phone import clean_phone as normalize\n\n\ndef alias_key(value: str) -> str:\n    return normalize(value)\n",
        "app/qualified.py": "import app.phone\n\n\ndef qualified_key(value: str) -> str:\n    return app.phone.clean_phone(value)\n",
        "app/public.py": "from app.phone import clean_phone\n\n__all__ = [\"clean_phone\"]\n",
        "ARCHITECTURE.md": "Normalizer implementations use canonicalize_* names. Existing exports remain compatible.\n",
        "tests/test_phone.py": "from app.direct import direct_key\n\n\ndef test_direct_key() -> None:\n    assert direct_key('(415) 555-0100') == '4155550100'\n",
    },
    hidden_tests='''from app.aliased import alias_key
from app.public import clean_phone
from app.qualified import qualified_key


def test_every_import_style_and_legacy_export() -> None:
    assert alias_key("+1 212") == "1212"
    assert qualified_key("44-20") == "4420"
    assert clean_phone("03 12") == "0312"
''',
    expectations=(
        StressExpectation("defines", "app/phone.py", "canonicalize_phone"),
        StressExpectation("calls", "app/direct.py", "canonicalize_phone"),
        StressExpectation("imports_from", "app/aliased.py", "canonicalize_phone", "app.phone"),
        StressExpectation("calls", "app/qualified.py", "canonicalize_phone"),
        StressExpectation("exports", "app/public.py", "clean_phone"),
    ),
)


KEYWORD_COMPATIBILITY = StressCase(
    name="keyword_compatible_api_rename",
    category="public-api",
    rationale="Internal naming should change without breaking legacy keyword callers.",
    baseline_files={
        **_INIT,
        "app/reports.py": '''def make_report(title: str, include_empty: bool = False) -> str:
    """Public compatibility API; internal builders use build_* names."""
    suffix = " [all]" if include_empty else ""
    return title.strip() + suffix
''',
        "app/dashboard.py": "from app.reports import make_report\n\n\ndef dashboard_title(name: str) -> str:\n    return make_report(title=name, include_empty=True)\n",
        "ARCHITECTURE.md": "Internal constructors use build_* names. Public call signatures are compatibility contracts.\n",
        "tests/test_reports.py": "from app.dashboard import dashboard_title\n\n\ndef test_dashboard_title() -> None:\n    assert dashboard_title(' Sales ') == 'Sales [all]'\n",
    },
    hidden_tests='''import inspect

from app.reports import make_report


def test_legacy_keyword_contract() -> None:
    assert make_report(title=" Empty ", include_empty=False) == "Empty"
    assert list(inspect.signature(make_report).parameters) == ["title", "include_empty"]
''',
    expectations=(
        StressExpectation("defines", "app/reports.py", "build_report"),
        StressExpectation("calls", "app/dashboard.py", "build_report"),
        StressExpectation("defines", "app/reports.py", "make_report"),
    ),
)


NESTED_BREAK = StressCase(
    name="nested_loop_break_scope",
    category="control-flow",
    rationale="Duplicated inner searches should be extracted without broadening break scope.",
    baseline_files={
        **_INIT,
        "app/search.py": '''def first_matches(groups: list[list[int]], target: int) -> list[int]:
    matches: list[int] = []
    for group in groups:
        for value in group:
            if value == target:
                matches.append(value)
                break
    return matches


def groups_containing(groups: list[list[int]], target: int) -> int:
    count = 0
    for group in groups:
        for value in group:
            if value == target:
                count += 1
                break
    return count
''',
        "ARCHITECTURE.md": "Repeated collection search policy belongs in one private helper.\n",
        "tests/test_search.py": "from app.search import first_matches\n\n\ndef test_match() -> None:\n    assert first_matches([[1, 2], [2, 3]], 2) == [2, 2]\n",
    },
    hidden_tests='''from app.search import first_matches, groups_containing


def test_break_only_finishes_current_group() -> None:
    groups = [[7, 7], [], [1, 7], [2, 3], [7]]
    assert first_matches(groups, 7) == [7, 7, 7]
    assert groups_containing(groups, 7) == 3
''',
    expectations=(
        StressExpectation("calls_private", "app/search.py", "first_matches"),
        StressExpectation("calls_private", "app/search.py", "groups_containing"),
    ),
)


INPUT_MUTATION = StressCase(
    name="normalization_preserves_input_ownership",
    category="mutation-aliasing",
    rationale="Duplicate normalization should be extracted without mutating caller lists.",
    baseline_files={
        **_INIT,
        "app/tags.py": '''def tag_line(tags: list[str]) -> str:
    normalized = [tag.strip().casefold() for tag in tags if tag.strip()]
    return ",".join(normalized)


def unique_tags(tags: list[str]) -> list[str]:
    normalized = [tag.strip().casefold() for tag in tags if tag.strip()]
    return list(dict.fromkeys(normalized))
''',
        "ARCHITECTURE.md": "Pure normalization helpers must not mutate caller-owned collections.\n",
        "tests/test_tags.py": "from app.tags import tag_line\n\n\ndef test_tag_line() -> None:\n    assert tag_line([' A ', '', 'B']) == 'a,b'\n",
    },
    hidden_tests='''from app.tags import tag_line, unique_tags


def test_input_is_not_mutated_or_reordered() -> None:
    tags = [" B ", "a", "B", "  "]
    before = list(tags)
    assert tag_line(tags) == "b,a,b"
    assert unique_tags(tags) == ["b", "a"]
    assert tags == before
''',
    expectations=(
        StressExpectation("calls_private", "app/tags.py", "tag_line"),
        StressExpectation("calls_private", "app/tags.py", "unique_tags"),
    ),
)


EXCEPTION_CHAIN = StressCase(
    name="exception_translation_preserves_cause",
    category="error-semantics",
    rationale="Duplicate parsing/translation should retain explicit exception chaining.",
    baseline_files={
        **_INIT,
        "app/config.py": '''class ConfigError(ValueError):
    pass


def worker_count(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"invalid worker count: {raw}") from exc


def retry_count(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"invalid retry count: {raw}") from exc
''',
        "ARCHITECTURE.md": "Parsing mechanics should be shared; domain-specific diagnostics remain at callers.\n",
        "tests/test_config.py": "from app.config import worker_count\n\n\ndef test_worker_count() -> None:\n    assert worker_count('4') == 4\n",
    },
    hidden_tests='''import pytest

from app.config import ConfigError, retry_count, worker_count


@pytest.mark.parametrize(("function", "label"), [(worker_count, "worker"), (retry_count, "retry")])
def test_translation_keeps_context_and_cause(function, label) -> None:
    with pytest.raises(ConfigError, match=f"invalid {label} count") as caught:
        function("many")
    assert isinstance(caught.value.__cause__, ValueError)
''',
    expectations=(
        StressExpectation("calls_private", "app/config.py", "worker_count"),
        StressExpectation("calls_private", "app/config.py", "retry_count"),
        StressExpectation("raise_from", "app/config.py"),
    ),
)


NONE_SENTINEL = StressCase(
    name="none_vs_missing_sentinel",
    category="type-semantics",
    rationale="Duplicate option resolution must distinguish explicit None from absence.",
    baseline_files={
        **_INIT,
        "app/options.py": '''MISSING = object()


def request_timeout(options: dict[str, object]) -> int | None:
    value = options.get("request_timeout", MISSING)
    if value is MISSING:
        return 30
    if value is None or isinstance(value, int):
        return value
    raise TypeError("request_timeout must be int or None")


def connect_timeout(options: dict[str, object]) -> int | None:
    value = options.get("connect_timeout", MISSING)
    if value is MISSING:
        return 10
    if value is None or isinstance(value, int):
        return value
    raise TypeError("connect_timeout must be int or None")
''',
        "ARCHITECTURE.md": "Option lookup policy is shared; explicit None disables a timeout and differs from missing.\n",
        "tests/test_options.py": "from app.options import request_timeout\n\n\ndef test_default() -> None:\n    assert request_timeout({}) == 30\n",
    },
    hidden_tests='''import pytest

from app.options import connect_timeout, request_timeout


def test_none_is_not_missing() -> None:
    assert request_timeout({"request_timeout": None}) is None
    assert connect_timeout({"connect_timeout": None}) is None
    assert connect_timeout({}) == 10


def test_bad_value_still_fails() -> None:
    with pytest.raises(TypeError, match="request_timeout"):
        request_timeout({"request_timeout": "30"})
''',
    expectations=(
        StressExpectation("calls_private", "app/options.py", "request_timeout"),
        StressExpectation("calls_private", "app/options.py", "connect_timeout"),
    ),
)


GENERATED_DECOY = StressCase(
    name="generated_vendor_decoy_unchanged",
    category="discovery-scope",
    rationale="A generated copy is a tempting duplicate but is explicitly outside edit scope.",
    baseline_files={
        **_INIT,
        "app/slug.py": '''def make_slug(value: str) -> str:
    """Deprecated internal name; use build_slug while keeping compatibility."""
    return "-".join(value.strip().casefold().split())
''',
        "app/page.py": "from app.slug import make_slug\n\n\ndef page_path(title: str) -> str:\n    return '/pages/' + make_slug(title)\n",
        "vendor/generated_slug.py": '''# GENERATED FILE - DO NOT EDIT
def make_slug(value: str) -> str:
    return "-".join(value.strip().casefold().split())
''',
        "ARCHITECTURE.md": "Builders use build_* names. Never modify vendor/ or generated files. Preserve compatibility aliases.\n",
        "tests/test_page.py": "from app.page import page_path\n\n\ndef test_page_path() -> None:\n    assert page_path('Hello World') == '/pages/hello-world'\n",
    },
    hidden_tests='''from app.slug import make_slug
from vendor.generated_slug import make_slug as generated_make_slug


def test_compatibility_and_generated_runtime() -> None:
    assert make_slug(" Old Name ") == "old-name"
    assert generated_make_slug(" Vendor Name ") == "vendor-name"
''',
    expectations=(
        StressExpectation("defines", "app/slug.py", "build_slug"),
        StressExpectation("calls", "app/page.py", "build_slug"),
        StressExpectation("defines", "app/slug.py", "make_slug"),
        StressExpectation("unchanged", "vendor/generated_slug.py"),
    ),
)


STABLE_SORT = StressCase(
    name="stable_sort_tie_order",
    category="performance-ordering",
    rationale="Duplicate ranking keys should be shared without adding a tie-breaker.",
    baseline_files={
        **_INIT,
        "app/ranking.py": '''def rank_active(items: list[dict[str, object]]) -> list[dict[str, object]]:
    active = [item for item in items if item.get("active") is True]
    return sorted(active, key=lambda item: -int(item["score"]))


def rank_all(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(items, key=lambda item: -int(item["score"]))
''',
        "ARCHITECTURE.md": "Ranking policy belongs in one key helper. Equal scores retain source order.\n",
        "tests/test_ranking.py": "from app.ranking import rank_all\n\n\ndef test_scores_descend() -> None:\n    rows = [{'id': 'a', 'score': 1}, {'id': 'b', 'score': 3}]\n    assert [row['id'] for row in rank_all(rows)] == ['b', 'a']\n",
    },
    hidden_tests='''from app.ranking import rank_active, rank_all


def test_equal_scores_remain_stable() -> None:
    rows = [
        {"id": "first", "score": 5, "active": True},
        {"id": "second", "score": 5, "active": True},
        {"id": "hidden", "score": 9, "active": False},
    ]
    assert [row["id"] for row in rank_all(rows)] == ["hidden", "first", "second"]
    assert [row["id"] for row in rank_active(rows)] == ["first", "second"]
''',
    expectations=(
        StressExpectation("calls_private", "app/ranking.py", "rank_active"),
        StressExpectation("calls_private", "app/ranking.py", "rank_all"),
    ),
)


STRESS_CASES: tuple[StressCase, ...] = (
    ALIASED_RENAME,
    KEYWORD_COMPATIBILITY,
    NESTED_BREAK,
    INPUT_MUTATION,
    EXCEPTION_CHAIN,
    NONE_SENTINEL,
    GENERATED_DECOY,
    STABLE_SORT,
)
CASES = STRESS_CASES


def structural_failures(case: StressCase, root: Path) -> list[str]:
    failures: list[str] = []
    for expectation in case.expectations:
        path = root / expectation.path
        if not path.is_file():
            failures.append(f"missing {expectation.path}")
            continue
        content = path.read_text()
        if expectation.kind == "unchanged":
            if content != case.baseline_files[expectation.path]:
                failures.append(f"changed protected path {expectation.path}")
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            failures.append(f"invalid Python in {expectation.path}")
            continue
        if not _matches(tree, expectation):
            failures.append(
                f"{expectation.kind} {expectation.symbol or '<structural>'} in {expectation.path}"
            )
    private_groups: dict[str, list[StressExpectation]] = {}
    for expectation in case.expectations:
        if expectation.kind == "calls_private":
            private_groups.setdefault(expectation.path, []).append(expectation)
    for relative, expectations in private_groups.items():
        if len(expectations) < 2:
            continue
        try:
            tree = ast.parse((root / relative).read_text())
        except (OSError, SyntaxError):
            continue
        shared = set.intersection(
            *(_private_calls(tree, expectation.symbol) for expectation in expectations)
        )
        if not shared:
            failures.append(f"no shared private helper across public functions in {relative}")
    return failures


def _matches(tree: ast.Module, expectation: StressExpectation) -> bool:
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
            isinstance(node, ast.ImportFrom)
            and any(alias.name == expectation.symbol for alias in node.names)
            for node in ast.walk(tree)
        ) or any(
            isinstance(node, ast.Constant) and node.value == expectation.symbol
            for node in ast.walk(tree)
        )
    if expectation.kind == "calls_private":
        return bool(_private_calls(tree, expectation.symbol))
    if expectation.kind == "raise_from":
        return any(isinstance(node, ast.Raise) and node.cause is not None for node in ast.walk(tree))
    raise ValueError(f"unsupported stress expectation: {expectation.kind}")


def _call_name(function: ast.expr) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _private_calls(tree: ast.Module, function_name: str) -> set[str]:
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        return set()
    return {
        name
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (name := _call_name(node.func)) is not None
        and name.startswith("_")
    }
