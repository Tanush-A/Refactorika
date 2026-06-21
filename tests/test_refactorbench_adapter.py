"""RefactorBench adapter: deterministic classification + provider-agnostic LLM NL->spec.

The LLM path is exercised fully offline via a stub client (no key, no network), proving the
eval is provider-agnostic and that token accounting is wired.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import refactorbench as rb  # noqa: E402
from refactorika.llm.client import LLMClient  # noqa: E402


def test_classify_symbol_rename_in_scope():
    s = rb.classify("Rename value_is_sequence to value_is_a_sequence and update usage.")
    assert s.in_scope and s.kind == "rename"
    assert s.params == {"old": "value_is_sequence", "new": "value_is_a_sequence"}


def test_classify_module_rename_declined():
    s = rb.classify("Rename params.py to param.py and update the whole repo.")
    assert not s.in_scope and s.kind == "module_rename"


def test_classify_move_declined():
    s = rb.classify("Move expand_router_string to celery/app/utils.py and import it.")
    assert not s.in_scope and s.kind == "move"


def test_classify_task_uses_llm_only_when_regex_fails():
    instruction = "Make get_thing known as fetch_thing throughout."  # not matched by the regex
    assert rb.classify(instruction).in_scope is False

    resp = {"in_scope": True, "kind": "rename", "old": "get_thing", "new": "fetch_thing",
            "reason": "symbol rename"}
    keyer = LLMClient()
    key = keyer.cache_key(rb._LLM_CLASSIFY_SYSTEM, rb._llm_classify_prompt(instruction))
    client = LLMClient(stub={key: resp})

    s = rb.classify_task(instruction, client=client)
    assert s.in_scope and s.params == {"old": "get_thing", "new": "fetch_thing"}


def test_llm_classify_declines_non_rename():
    instruction = "Combine two functions into one."
    keyer = LLMClient()
    key = keyer.cache_key(rb._LLM_CLASSIFY_SYSTEM, rb._llm_classify_prompt(instruction))
    client = LLMClient(stub={key: {"in_scope": False, "kind": "none", "reason": "not a rename"}})
    assert rb.llm_classify(instruction, client) is None


def test_token_usage_accumulates_on_live_path(tmp_path):
    from refactorika.llm.providers import GenerationProvider

    class _FakeProvider(GenerationProvider):
        name = "fake"

        def __init__(self):
            super().__init__("fake-model")

        def available(self):
            return True

        def complete(self, messages, **opts):
            self.last_usage = {"input": 10, "output": 5}
            return '{"ok": true}'

    # Isolated cache so this doesn't read/write the shared on-disk cache.
    client = LLMClient(provider=_FakeProvider(), cache_path=str(tmp_path / "c.json"))
    client.complete_json("sys", "prompt-a")
    client.complete_json("sys", "prompt-b")
    assert client.total_usage == {"input": 20, "output": 10, "calls": 2}
