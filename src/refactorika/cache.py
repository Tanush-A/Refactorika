import hashlib
import os
from typing import Any

import redis

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def cache_key(tool: str, source: str) -> str:
    digest = hashlib.sha256(source.encode()).hexdigest()
    return f"refactorika:{tool}:{digest}"


def get(key: str) -> Any | None:
    return _get_client().get(key)


def set(key: str, value: str, ttl_seconds: int = 3600) -> None:
    _get_client().setex(key, ttl_seconds, value)
