import json
import urllib.error
from typing import Any

import pytest
from eval.agents.prompts import (
    AGENTIC_HARNESS_SYSTEM,
    AGENTIC_SYSTEM,
    FOUR_ARM_CONTRACT,
    build_edit_prompt,
    build_harness_context_prompt,
    build_off_prompt,
)
from eval.agents.providers import HttpProvider


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_anthropic_completion_normalizes_usage_and_request() -> None:
    requests: list[tuple[Any, float]] = []

    def opener(request: Any, timeout: float) -> _Response:
        requests.append((request, timeout))
        return _Response(
            {
                "content": [{"type": "text", "text": '{"app.py":"new"}'}],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 2,
                },
            }
        )

    provider = HttpProvider("anthropic", "sonnet", api_key="secret", timeout=12, opener=opener)
    result = provider.complete("refactor this codebase")

    assert result.text == '{"app.py":"new"}'
    assert result.usage.total == 23
    assert requests[0][1] == 12
    body = json.loads(requests[0][0].data)
    assert body["temperature"] == 0
    assert body["messages"][0]["content"] == "refactor this codebase"


def test_openai_completion_accounts_for_cached_prompt_tokens() -> None:
    def opener(_request: Any, timeout: float) -> _Response:
        assert timeout == 9
        return _Response(
            {
                "choices": [{"message": {"content": "patch"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "prompt_tokens_details": {"cached_tokens": 6},
                },
            }
        )

    result = HttpProvider("openai", "local", timeout=9, opener=opener).complete("prompt")
    assert result.usage.input_tokens == 4
    assert result.usage.cache_read_tokens == 6
    assert result.usage.output_tokens == 4


def test_configuration_and_transport_failures_are_classified() -> None:
    missing = HttpProvider("anthropic", "sonnet").complete("prompt")
    assert missing.error_class == "configuration_failure"

    def failing(_request: Any, timeout: float) -> _Response:
        raise urllib.error.URLError(f"timed out after {timeout}")

    failed = HttpProvider("anthropic", "sonnet", api_key="secret", opener=failing).complete(
        "prompt"
    )
    assert failed.error_class == "provider_failure"


def test_tool_completion_classifies_malformed_responses() -> None:
    provider = HttpProvider(
        "anthropic",
        "sonnet",
        api_key="secret",
        opener=lambda _request, timeout: _Response({"content": "invalid"}),
    )
    result = provider.complete_tools(
        [{"role": "user", "content": "refactor"}],
        system=AGENTIC_SYSTEM,
        tools=[],
        arm="agentic",
    )
    assert result.error_class == "malformed_response"
    assert result.error and result.error.startswith("malformed_provider_response:")


def test_prompt_contracts_preserve_oracle_isolation_and_arm_parity() -> None:
    snapshot = {"app.py": "def f(): pass\n"}
    off = build_off_prompt("refactor this codebase", snapshot)
    harness = build_harness_context_prompt(
        "refactor this codebase",
        audit_plan={"opportunities": []},
        architecture_notes={},
    )
    edit = build_edit_prompt("refactor this codebase", snapshot, harness, failure="lint")

    assert "refactor this codebase" in off
    assert "tests/oracle" not in off + harness + edit
    assert "lint" in edit
    assert "agentic+harness" in FOUR_ARM_CONTRACT
    assert "developer tools" in AGENTIC_SYSTEM
    assert "developer tools" in AGENTIC_HARNESS_SYSTEM


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported provider"):
        HttpProvider("other", "model")
