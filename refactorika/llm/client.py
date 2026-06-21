"""Record/replay LLM client, layered above any GenerationProvider.

The cache sits one level above the provider and is keyed by (provider, model, prompt), so a
recorded run replays identically under Claude or Ollama — for reproducible demos and eval. A
`stub` mapping injects responses for tests (no network). With no provider key/reachability and
no cache/stub hit, `complete_json` returns None so callers degrade to the deterministic plan —
the engine never *depends* on the model being reachable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from refactorika.llm.providers import GenerationProvider, get_generation_provider


class LLMClient:
    def __init__(
        self,
        provider: Optional[GenerationProvider] = None,
        cache_path: str = ".refactorika/llm_cache.json",
        stub: Optional[dict[str, dict]] = None,
        replay_only: bool = False,
    ):
        self.provider = provider or get_generation_provider()
        self.model = self.provider.model
        self.cache_path = Path(cache_path)
        self.stub = stub or {}
        self.replay_only = replay_only
        self._cache = self._load_cache()
        # Accumulated token usage across *live* calls (cache/stub hits cost nothing).
        self.total_usage: dict = {"input": 0, "output": 0, "calls": 0}

    # ------------------------------------------------------------------ public
    def available(self) -> bool:
        """True if we can answer: a stub, a populated replay cache, or a live provider."""
        return bool(self.stub) or bool(self._cache) or self.provider.available()

    def cache_key(self, system: str, prompt: str) -> str:
        """Stable key for (provider, model, system, prompt) — also used by tests to build stubs."""
        material = f"{self.provider.name}\0{self.model}\0{system}\0{prompt}"
        return hashlib.sha256(material.encode()).hexdigest()[:32]

    def complete_json(self, system: str, prompt: str) -> Optional[dict]:
        """Parsed JSON from the model, or None. Order: stub -> cache -> live provider."""
        key = self.cache_key(system, prompt)
        if key in self.stub:
            return self.stub[key]
        if key in self._cache:
            return self._cache[key]
        if self.replay_only or not self.provider.available():
            return None
        raw = self.provider.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        )
        usage = getattr(self.provider, "last_usage", {}) or {}
        self.total_usage["input"] += usage.get("input", 0)
        self.total_usage["output"] += usage.get("output", 0)
        self.total_usage["calls"] += 1
        if raw is None:
            return None
        parsed = _extract_json(raw)
        if parsed is not None:
            self._cache[key] = parsed
            self._save_cache()
        return parsed

    # ----------------------------------------------------------------- internal
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
        for part in text.split("```"):
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
