"""Live integration test: real Redis Cloud hybrid search + real OpenAI embeddings.

Runs ONLY when redisvl is importable, REDIS_URL points at a reachable Redis with
the Query Engine (FT.*), and OPENAI_API_KEY is set — otherwise skipped, so the
offline suite never depends on it. Marked `real_embeddings` so conftest's
network-disabling fixture does not apply.

To run once Redis Cloud is reachable:
    PATH=.venv/bin:$PATH .venv/bin/python -m pytest tests/test_hybrid_live.py -q
"""

import os

import pytest
from refactorika.core.storage import _load_dotenv

_load_dotenv()


def _hybrid_ready() -> tuple[bool, str]:
    if not os.environ.get("OPENAI_API_KEY"):
        return False, "no OPENAI_API_KEY"
    url = os.environ.get("REDIS_URL")
    if not url:
        return False, "no REDIS_URL"
    try:
        import redisvl  # noqa: F401
    except Exception:
        return False, "redisvl not installed"
    try:
        import redis

        r = redis.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3
        )
        r.ping()
        r.execute_command("FT._LIST")  # Query Engine present?
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"redis not ready: {str(e)[:50]}"


_ready, _reason = _hybrid_ready()
pytestmark = [
    pytest.mark.real_embeddings,
    pytest.mark.skipif(not _ready, reason=f"live Redis hybrid required ({_reason})"),
]


def test_hybrid_index_is_live_and_finds_semantic_twin() -> None:
    from refactorika.analysis import embeddings
    from refactorika.core.storage import Storage
    from refactorika.memory.vector_index import VectorIndex

    storage = Storage()  # real REDIS_URL
    vi = VectorIndex(storage)
    assert vi._use_redisvl is True, "expected the RedisVL hybrid index to be live"
    vi.drop()  # clean slate
    vi = VectorIndex(storage)  # recreate the index

    # Two semantically-equivalent functions (sum a list) + one unrelated.
    docs = {
        "m.compute_total": (
            "def compute_total(items):\n    total = 0\n"
            "    for i in items:\n        total += i\n    return total\n"
        ),
        "m.sum_values": "def sum_values(values):\n    return sum(values)\n",
        "m.greet": "def greet(name):\n    return f'hello {name}'\n",
    }
    vecs = embeddings.embed(list(docs.values()))
    assert len(vecs[0]) == 1536  # text-embedding-3-small
    for (key, text), vec in zip(docs.items(), vecs):
        name = key.split(".")[-1]
        vi.upsert(
            key, vec,
            meta={"file": "m.py", "name": name, "module": "m", "line": 1},
            text=text,
        )

    # Hybrid query with compute_total: its semantic twin sum_values should rank
    # above the unrelated greet, via FT.HYBRID (BM25 + vector, RRF).
    neighbors = vi.query_hybrid(vecs[0], docs["m.compute_total"], k=3)
    others = [n.key for n in neighbors if n.key != "m.compute_total"]
    assert others, "expected neighbors back from the live index"
    assert others[0] == "m.sum_values", f"expected sum_values top, got {others}"
    # meta round-trips through Redis (the meta-drop bug is fixed).
    assert any(n.meta.get("module") == "m" for n in neighbors)

    vi.drop()
