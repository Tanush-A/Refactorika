"""State: edit log + analysis cache. Redis by default, local JSON fallback.

Redis is the primary backend (`REDIS_URL`, or localhost when unset). The JSON
fallback is mandatory — if Redis is unreachable the demo must still run offline.
Redis is an optimization, never a hard dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_LOG_KEY = "refactorika:log"
_CACHE_KEY = "refactorika:cache"
_PLAN_KEY = "refactorika:plan"

# Tried when neither the constructor nor REDIS_URL specifies one. Pass
# redis_url=None explicitly to force the JSON backend (used by tests).
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_UNSET = object()


def _load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a .env file (KEY=VALUE), never overriding what's already set."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


class Storage:
    def __init__(self, redis_url=_UNSET, json_path: Optional[Path] = None):
        _load_dotenv()
        if redis_url is _UNSET:  # not specified -> env, then localhost default
            redis_url = os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL)
        self.json_path = Path(
            json_path or os.environ.get("REFACTORIKA_STATE", ".refactorika/state.json")
        )
        self._redis = self._connect(redis_url)
        self.backend = "redis" if self._redis else "json"

    def _connect(self, url: Optional[str]):
        if not url:  # explicit None/"" -> JSON backend
            return None
        try:
            import redis  # noqa: PLC0415

            client = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=0.5
            )
            client.ping()
            return client
        except Exception:
            return None  # unreachable / not installed -> fast JSON fallback

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

    def count_attempts(self, file: str | list[str]) -> int:
        """Prior non-committed attempts touching any of these file(s)."""
        targets = {file} if isinstance(file, str) else set(file)
        return sum(
            1
            for r in self.get_log()
            if r["status"] != "committed"
            and (targets & set(r.get("files") or [r["file"]]))
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

    # --- current refactor plan (single plan, overwritten) ----------------------
    def save_plan(self, plan: dict) -> None:
        if self._redis:
            self._redis.set(_PLAN_KEY, json.dumps(plan))
            return
        data = self._read_json()
        data["plan"] = plan
        self._write_json(data)

    def load_plan(self) -> dict | None:
        if self._redis:
            raw = self._redis.get(_PLAN_KEY)
            return json.loads(raw) if raw else None
        return self._read_json().get("plan")

    # --- vector index fallback (brute-force when RediSearch unavailable) -------
    def vector_upsert(self, key: str, entry: dict) -> None:
        """Persist a vector entry {vector, meta} to JSON fallback."""
        if self._redis:
            self._redis.hset("refactorika:vectors", key, json.dumps(entry))
            return
        data = self._read_json()
        data.setdefault("vectors", {})[key] = entry
        self._write_json(data)

    def vector_get_all(self) -> dict:
        """Return all vector entries keyed by their string key."""
        if self._redis:
            raw = self._redis.hgetall("refactorika:vectors")
            return {k: json.loads(v) for k, v in raw.items()}
        return self._read_json().get("vectors", {})

    def vector_delete_all(self) -> None:
        if self._redis:
            self._redis.delete("refactorika:vectors")
            return
        data = self._read_json()
        data.pop("vectors", None)
        self._write_json(data)

    # --- JSON state helpers for low-level access (used by memory/ modules) -----
    def _read_state(self) -> dict:
        return self._read_json()

    def _write_state(self, data: dict) -> None:
        self._write_json(data)
