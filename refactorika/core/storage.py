"""State: edit log + analysis cache. Redis when reachable, local JSON otherwise.

The fallback is mandatory — the demo must run offline. Redis is an optimization,
never a dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_LOG_KEY = "refactorika:log"
_CACHE_KEY = "refactorika:cache"


class Storage:
    def __init__(self, redis_url: Optional[str] = None, json_path: Optional[Path] = None):
        self.json_path = Path(
            json_path or os.environ.get("REFACTORIKA_STATE", ".refactorika/state.json")
        )
        self._redis = self._connect(redis_url or os.environ.get("REDIS_URL"))
        self.backend = "redis" if self._redis else "json"

    def _connect(self, url: Optional[str]):
        if not url:
            return None
        try:
            import redis  # noqa: PLC0415

            client = redis.Redis.from_url(url, decode_responses=True)
            client.ping()
            return client
        except Exception:
            return None  # any failure -> JSON fallback

    # --- JSON fallback helpers -------------------------------------------------
    def _read_json(self) -> dict:
        if self.json_path.exists():
            return json.loads(self.json_path.read_text())
        return {"log": [], "cache": {}}

    def _write_json(self, data: dict) -> None:
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(json.dumps(data, indent=2))

    # --- edit log --------------------------------------------------------------
    def append_log(self, record: dict) -> None:
        if self._redis:
            self._redis.rpush(_LOG_KEY, json.dumps(record))
            return
        data = self._read_json()
        data["log"].append(record)
        self._write_json(data)

    def get_log(self) -> list[dict]:
        if self._redis:
            return [json.loads(r) for r in self._redis.lrange(_LOG_KEY, 0, -1)]
        return self._read_json()["log"]

    def count_attempts(self, file: str) -> int:
        """Prior non-committed attempts for a file -> the retry index of the next edit."""
        return sum(
            1 for r in self.get_log() if r["file"] == file and r["status"] != "committed"
        )

    # --- analysis cache (keyed on normalized AST signature) --------------------
    def cache_get(self, key: str) -> Optional[dict]:
        if self._redis:
            raw = self._redis.hget(_CACHE_KEY, key)
            return json.loads(raw) if raw else None
        return self._read_json()["cache"].get(key)

    def cache_set(self, key: str, value: dict) -> None:
        if self._redis:
            self._redis.hset(_CACHE_KEY, key, json.dumps(value))
            return
        data = self._read_json()
        data["cache"][key] = value
        self._write_json(data)
