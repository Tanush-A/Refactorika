"""Additional stress fixtures for subtle behavior-preserving refactors."""

from __future__ import annotations

from .stress import StressCase, StressExpectation

# Fixture source is kept inline and some lines intentionally mirror realistic code.
# ruff: noqa: E501


_INIT = {"app/__init__.py": "", "tests/__init__.py": ""}


NUMERIC_THRESHOLD = StressCase(
    name="numeric_threshold_inclusivity",
    category="numeric-boundaries",
    rationale="A shared tier calculation must retain inclusive threshold boundaries.",
    baseline_files={
        **_INIT,
        "app/fees.py": """def domestic_fee(cents: int) -> int:
    if cents <= 1_000:
        return 25
    if cents <= 10_000:
        return 75
    return 150


def international_fee(cents: int) -> int:
    if cents <= 1_000:
        return 50
    if cents <= 10_000:
        return 150
    return 300
""",
        "ARCHITECTURE.md": "Fee tier selection belongs in one private _fee_for helper. Boundaries are inclusive.\n",
        "tests/test_fees.py": "from app.fees import domestic_fee\n\n\ndef test_domestic_fee() -> None:\n    assert domestic_fee(5000) == 75\n",
    },
    hidden_tests="""from app.fees import domestic_fee, international_fee


def test_inclusive_thresholds_and_neighbors() -> None:
    assert [domestic_fee(value) for value in (1000, 1001, 10000, 10001)] == [25, 75, 75, 150]
    assert [international_fee(value) for value in (1000, 1001, 10000, 10001)] == [50, 150, 150, 300]
""",
    expectations=(
        StressExpectation("calls_private", "app/fees.py", "domestic_fee"),
        StressExpectation("calls_private", "app/fees.py", "international_fee"),
    ),
)


ROUNDING_SEQUENCE = StressCase(
    name="integer_rounding_sequence",
    category="numeric-boundaries",
    rationale="Combining arithmetic must not move integer truncation across operations.",
    baseline_files={
        **_INIT,
        "app/totals.py": """def standard_total(cents: int) -> int:
    discounted = cents * 85 // 100
    return discounted + discounted * 725 // 10_000


def member_total(cents: int) -> int:
    discounted = cents * 80 // 100
    return discounted + discounted * 725 // 10_000
""",
        "ARCHITECTURE.md": "Pricing uses one private _discounted_total helper. Discount truncates before tax.\n",
        "tests/test_totals.py": "from app.totals import standard_total\n\n\ndef test_total() -> None:\n    assert standard_total(10000) == 9116\n",
    },
    hidden_tests="""from app.totals import member_total, standard_total


def test_small_values_expose_rounding_order() -> None:
    assert [standard_total(value) for value in (1, 99, 101, 199)] == [0, 90, 91, 181]
    assert [member_total(value) for value in (1, 99, 101, 199)] == [0, 84, 85, 170]
""",
    expectations=(
        StressExpectation("calls_private", "app/totals.py", "standard_total"),
        StressExpectation("calls_private", "app/totals.py", "member_total"),
    ),
)


LOOP_CONTINUE = StressCase(
    name="loop_guard_continue_scope",
    category="control-flow",
    rationale="Extracted predicates must skip only the current item, never the whole function.",
    baseline_files={
        **_INIT,
        "app/events.py": """def event_ids(events: list[dict[str, object]]) -> list[str]:
    result: list[str] = []
    for event in events:
        if event.get("enabled") is not True:
            continue
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            continue
        result.append(event_id)
    return result


def event_count(events: list[dict[str, object]]) -> int:
    count = 0
    for event in events:
        if event.get("enabled") is not True:
            continue
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            continue
        count += 1
    return count
""",
        "ARCHITECTURE.md": "Event eligibility belongs in one private _is_eligible helper. Iteration remains exhaustive.\n",
        "tests/test_events_extra.py": "from app.events import event_ids\n\n\ndef test_ids() -> None:\n    assert event_ids([{'enabled': True, 'id': 'a'}]) == ['a']\n",
    },
    hidden_tests="""from app.events import event_count, event_ids


def test_invalid_prefix_does_not_abort_iteration() -> None:
    events = [
        {"enabled": False, "id": "off"},
        {"enabled": True, "id": ""},
        {"enabled": True, "id": "a"},
        {"enabled": True, "id": "b"},
    ]
    assert event_ids(events) == ["a", "b"]
    assert event_count(events) == 2
""",
    expectations=(
        StressExpectation("calls_private", "app/events.py", "event_ids"),
        StressExpectation("calls_private", "app/events.py", "event_count"),
    ),
)


ALIAS_OWNERSHIP = StressCase(
    name="nested_alias_ownership",
    category="mutation-aliasing",
    rationale="Normalization may copy records but must not mutate nested caller-owned lists.",
    baseline_files={
        **_INIT,
        "app/records.py": """def normalized_names(record: dict[str, object]) -> list[str]:
    raw = record.get("names", [])
    if not isinstance(raw, list):
        return []
    return [str(name).strip().casefold() for name in raw]


def normalized_record(record: dict[str, object]) -> dict[str, object]:
    raw = record.get("names", [])
    names = [] if not isinstance(raw, list) else [str(name).strip().casefold() for name in raw]
    result = dict(record)
    result["names"] = names
    return result
""",
        "ARCHITECTURE.md": "Name conversion belongs in private _normalized_names and never mutates input aliases.\n",
        "tests/test_records.py": "from app.records import normalized_names\n\n\ndef test_names() -> None:\n    assert normalized_names({'names': [' A ']}) == ['a']\n",
    },
    hidden_tests="""from app.records import normalized_names, normalized_record


def test_nested_input_alias_is_unchanged() -> None:
    names = [" B ", "A"]
    source = {"names": names, "kind": "person"}
    assert normalized_names(source) == ["b", "a"]
    assert normalized_record(source) == {"names": ["b", "a"], "kind": "person"}
    assert source["names"] is names
    assert names == [" B ", "A"]
""",
    expectations=(
        StressExpectation("defines", "app/records.py", "_normalized_names"),
        StressExpectation("calls_private", "app/records.py", "normalized_names"),
        StressExpectation("calls_private", "app/records.py", "normalized_record"),
    ),
)


KEY_ERROR_IDENTITY = StressCase(
    name="key_error_payload_identity",
    category="error-semantics",
    rationale="Shared lookup must preserve KeyError's original argument, not stringify it.",
    baseline_files={
        **_INIT,
        "app/lookup.py": """def required_host(config: dict[str, str]) -> str:
    try:
        return config["host"]
    except KeyError:
        raise KeyError("host")


def required_region(config: dict[str, str]) -> str:
    try:
        return config["region"]
    except KeyError:
        raise KeyError("region")
""",
        "ARCHITECTURE.md": "Required-key lookup belongs in one private _required helper. Preserve native KeyError args.\n",
        "tests/test_lookup.py": "from app.lookup import required_host\n\n\ndef test_host() -> None:\n    assert required_host({'host': 'db'}) == 'db'\n",
    },
    hidden_tests="""import pytest

from app.lookup import required_host, required_region


@pytest.mark.parametrize(("function", "key"), [(required_host, "host"), (required_region, "region")])
def test_missing_key_payload(function, key) -> None:
    with pytest.raises(KeyError) as caught:
        function({})
    assert caught.value.args == (key,)
    assert caught.value.__cause__ is None
""",
    expectations=(
        StressExpectation("calls_private", "app/lookup.py", "required_host"),
        StressExpectation("calls_private", "app/lookup.py", "required_region"),
    ),
)


ERROR_CHAIN_CONTEXT = StressCase(
    name="domain_error_chain_context",
    category="error-semantics",
    rationale="A shared decoder must retain domain labels and explicit underlying causes.",
    baseline_files={
        **_INIT,
        "app/decoding.py": """class DecodeError(ValueError):
    pass


def decode_page(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise DecodeError(f"invalid page: {raw}") from exc


def decode_limit(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise DecodeError(f"invalid limit: {raw}") from exc
""",
        "ARCHITECTURE.md": "Integer decoding belongs in private _decode_int; callers retain domain labels and chaining.\n",
        "tests/test_decoding.py": "from app.decoding import decode_page\n\n\ndef test_page() -> None:\n    assert decode_page('4') == 4\n",
    },
    hidden_tests="""import pytest

from app.decoding import DecodeError, decode_limit, decode_page


@pytest.mark.parametrize(("function", "label"), [(decode_page, "page"), (decode_limit, "limit")])
def test_error_message_and_cause(function, label) -> None:
    with pytest.raises(DecodeError, match=f"invalid {label}: many") as caught:
        function("many")
    assert isinstance(caught.value.__cause__, ValueError)
""",
    expectations=(
        StressExpectation("calls_private", "app/decoding.py", "decode_page"),
        StressExpectation("calls_private", "app/decoding.py", "decode_limit"),
        StressExpectation("raise_from", "app/decoding.py"),
    ),
)


GENERATOR_CLOSE = StressCase(
    name="generator_close_cleanup",
    category="generators-cleanup",
    rationale="Extracting generator mechanics must preserve cleanup on exhaustion and close().",
    baseline_files={
        **_INIT,
        "app/streaming.py": """def stream_values(values: list[int], events: list[str]):
    events.append("values:open")
    try:
        for value in values:
            yield value
    finally:
        events.append("values:close")


def stream_doubled(values: list[int], events: list[str]):
    events.append("doubled:open")
    try:
        for value in values:
            yield value * 2
    finally:
        events.append("doubled:close")
""",
        "ARCHITECTURE.md": "Generator lifecycle belongs in private _stream. Cleanup must run when consumers close early.\n",
        "tests/test_streaming.py": "from app.streaming import stream_values\n\n\ndef test_values() -> None:\n    assert list(stream_values([1, 2], [])) == [1, 2]\n",
    },
    hidden_tests="""from app.streaming import stream_doubled, stream_values


def test_close_runs_cleanup_once() -> None:
    events: list[str] = []
    stream = stream_values([1, 2], events)
    assert next(stream) == 1
    stream.close()
    assert events == ["values:open", "values:close"]


def test_exhaustion_runs_variant_cleanup() -> None:
    events: list[str] = []
    assert list(stream_doubled([2, 3], events)) == [4, 6]
    assert events == ["doubled:open", "doubled:close"]
""",
    expectations=(
        StressExpectation("calls_private", "app/streaming.py", "stream_values"),
        StressExpectation("calls_private", "app/streaming.py", "stream_doubled"),
    ),
)


RECURSIVE_CYCLES = StressCase(
    name="recursive_cycle_identity",
    category="recursion-cycles",
    rationale="Shared traversal must track object identity and terminate on self-referential graphs.",
    baseline_files={
        **_INIT,
        "app/graph.py": """def reachable_names(root: dict[str, object]) -> list[str]:
    names: list[str] = []
    seen: set[int] = set()

    def visit(node: dict[str, object]) -> None:
        identity = id(node)
        if identity in seen:
            return
        seen.add(identity)
        name = node.get("name")
        if isinstance(name, str):
            names.append(name)
        children = node.get("children", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    visit(root)
    return names


def reachable_count(root: dict[str, object]) -> int:
    seen: set[int] = set()

    def visit(node: dict[str, object]) -> int:
        identity = id(node)
        if identity in seen:
            return 0
        seen.add(identity)
        total = 1
        children = node.get("children", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    total += visit(child)
        return total

    return visit(root)
""",
        "ARCHITECTURE.md": "Graph visitation belongs in module-private _walk and deduplicates by object identity.\n",
        "tests/test_graph.py": "from app.graph import reachable_count\n\n\ndef test_count() -> None:\n    assert reachable_count({'name': 'a', 'children': []}) == 1\n",
    },
    hidden_tests="""from app.graph import reachable_count, reachable_names


def test_cycle_and_shared_child_are_visited_once() -> None:
    shared = {"name": "shared", "children": []}
    root = {"name": "root", "children": [shared, shared]}
    shared["children"].append(root)
    assert reachable_names(root) == ["root", "shared"]
    assert reachable_count(root) == 2


def test_equal_distinct_nodes_are_not_collapsed() -> None:
    left = {"name": "same", "children": []}
    right = {"name": "same", "children": []}
    root = {"name": "root", "children": [left, right]}
    assert reachable_names(root) == ["root", "same", "same"]
    assert reachable_count(root) == 3
""",
    expectations=(
        StressExpectation("defines", "app/graph.py", "_walk"),
        StressExpectation("calls_private", "app/graph.py", "reachable_names"),
        StressExpectation("calls_private", "app/graph.py", "reachable_count"),
    ),
)


FIRST_SEEN_ORDER = StressCase(
    name="first_seen_group_order",
    category="ordering",
    rationale="Consolidation must preserve first-seen group and member ordering.",
    baseline_files={
        **_INIT,
        "app/grouping.py": """def grouped_names(rows: list[tuple[str, str]]) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = {}
    for group, name in rows:
        if group not in groups:
            groups[group] = []
        groups[group].append(name)
    return list(groups.items())


def group_sizes(rows: list[tuple[str, str]]) -> list[tuple[str, int]]:
    groups: dict[str, list[str]] = {}
    for group, name in rows:
        if group not in groups:
            groups[group] = []
        groups[group].append(name)
    return [(group, len(names)) for group, names in groups.items()]
""",
        "ARCHITECTURE.md": "First-seen grouping belongs in private _group_rows. Never alphabetize groups or members.\n",
        "tests/test_grouping.py": "from app.grouping import group_sizes\n\n\ndef test_sizes() -> None:\n    assert group_sizes([('a', 'x'), ('a', 'y')]) == [('a', 2)]\n",
    },
    hidden_tests="""from app.grouping import group_sizes, grouped_names


def test_first_seen_order_at_both_levels() -> None:
    rows = [("z", "second"), ("a", "only"), ("z", "first")]
    assert grouped_names(rows) == [("z", ["second", "first"]), ("a", ["only"])]
    assert group_sizes(rows) == [("z", 2), ("a", 1)]
""",
    expectations=(
        StressExpectation("calls_private", "app/grouping.py", "grouped_names"),
        StressExpectation("calls_private", "app/grouping.py", "group_sizes"),
    ),
)


FINALLY_RETURN = StressCase(
    name="cleanup_does_not_mask_return",
    category="generators-cleanup",
    rationale="Shared resource handling must close exactly once without replacing return values.",
    baseline_files={
        **_INIT,
        "app/resources.py": """class Resource:
    def __init__(self, value: str, events: list[str]) -> None:
        self.value = value
        self.events = events

    def close(self) -> None:
        self.events.append("closed")


def read_upper(resource: Resource) -> str:
    try:
        return resource.value.upper()
    finally:
        resource.close()


def read_length(resource: Resource) -> int:
    try:
        return len(resource.value)
    finally:
        resource.close()
""",
        "ARCHITECTURE.md": "Resource execution belongs in private _using. Cleanup runs once and never masks results.\n",
        "tests/test_resources.py": "from app.resources import Resource, read_upper\n\n\ndef test_upper() -> None:\n    assert read_upper(Resource('ab', [])) == 'AB'\n",
    },
    hidden_tests="""from app.resources import Resource, read_length, read_upper


def test_result_and_single_cleanup() -> None:
    upper_events: list[str] = []
    length_events: list[str] = []
    assert read_upper(Resource("abc", upper_events)) == "ABC"
    assert read_length(Resource("abc", length_events)) == 3
    assert upper_events == ["closed"]
    assert length_events == ["closed"]
""",
    expectations=(
        StressExpectation("calls_private", "app/resources.py", "read_upper"),
        StressExpectation("calls_private", "app/resources.py", "read_length"),
    ),
)


STRESS_CASES: tuple[StressCase, ...] = (
    NUMERIC_THRESHOLD,
    ROUNDING_SEQUENCE,
    LOOP_CONTINUE,
    ALIAS_OWNERSHIP,
    KEY_ERROR_IDENTITY,
    ERROR_CHAIN_CONTEXT,
    GENERATOR_CLOSE,
    RECURSIVE_CYCLES,
    FIRST_SEEN_ORDER,
    FINALLY_RETURN,
)
CASES = STRESS_CASES
