"""Anthropic client with a deterministic record/replay cache.

Every call is keyed by a hash of (model, system, prompt). A cache hit replays the prior
response — so a demo is reproducible and a re-run costs nothing. A `stub` mapping lets
tests inject responses with no network. With no API key and no cache/stub hit,
``complete_json`` returns ``None`` so callers degrade to the deterministic plan rather
than crashing — the engine never *depends* on the LLM being reachable.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

# The current, most capable Sonnet — judgment quality matters here.
DEFAULT_MODEL = "claude-sonnet-4-6"


class LLMClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        cache_path: str = ".refactorika/llm_cache.json",
        stub: Optional[dict[str, dict]] = None,
        replay_only: bool = False,
    ):
        self.model = model
        self.cache_path = Path(cache_path)
        self.stub = stub or {}
        self.replay_only = replay_only
        self._cache = self._load_cache()
        self._client = None  # lazy

    # ------------------------------------------------------------------ public
    def available(self) -> bool:
        """True if the LLM can answer: a stub, a non-empty replay cache, or an API key.

        Counting a populated cache means a pre-baked (recorded) cache replays fully offline —
        the demo shows the LLM beats with no key set."""
        return bool(self.stub) or bool(self._cache) or bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete_json(self, system: str, prompt: str) -> Optional[dict]:
        """Return a parsed JSON object from the model, or None if unavailable.

        Resolution order: stub -> cache -> live API (unless replay_only). Live results
        are written back to the cache.
        """
        key = self._key(system, prompt)
        if key in self.stub:
            return self.stub[key]
        if key in self._cache:
            return self._cache[key]
        if self.replay_only or not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        raw = self._call_api(system, prompt)
        if raw is None:
            return None
        parsed = _extract_json(raw)
        if parsed is not None:
            self._cache[key] = parsed
            self._save_cache()
        return parsed

    # ----------------------------------------------------------------- internal
    def _key(self, system: str, prompt: str) -> str:
        h = hashlib.sha256(f"{self.model}\0{system}\0{prompt}".encode()).hexdigest()
        return h[:32]

    def _call_api(self, system: str, prompt: str) -> Optional[str]:
        try:
            if self._client is None:
                import anthropic

                self._client = anthropic.Anthropic()
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=2000,
                temperature=0,  # judgment should be reproducible
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in msg.content if block.type == "text")
        except Exception:
            return None

    def _load_cache(self) -> dict:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text())
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=2))
        except Exception:
            pass


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model response (handles ```json fences)."""
    text = text.strip()
    if "```" in text:
        # take the content of the first fenced block
        parts = text.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def stub_key(model: str, system: str, prompt: str) -> str:
    """Helper for tests: compute the cache key for a (model, system, prompt)."""
    h = hashlib.sha256(f"{model}\0{system}\0{prompt}".encode()).hexdigest()
    return h[:32]
