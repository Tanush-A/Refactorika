"""Tests for VectorIndex JSON fallback: upsert, query, cosine correctness."""

from pathlib import Path

from refactorika.core.storage import Storage
from refactorika.memory.vector_index import VectorIndex, _cosine


def _make(tmp_path: Path) -> tuple[Storage, VectorIndex]:
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    vi = VectorIndex(storage)
    return storage, vi


def test_upsert_and_query_nearest(tmp_path: Path) -> None:
    _, vi = _make(tmp_path)
    vi.upsert("a", [1.0, 0.0], {"name": "a"})
    vi.upsert("b", [0.9, 0.1], {"name": "b"})
    vi.upsert("c", [0.0, 1.0], {"name": "c"})

    results = vi.query([1.0, 0.0], k=2, threshold=0.0)
    assert len(results) == 2
    assert results[0].key == "a"  # identical vector → highest score
    assert results[0].score > results[1].score


def test_threshold_filters(tmp_path: Path) -> None:
    _, vi = _make(tmp_path)
    vi.upsert("near", [1.0, 0.0], {})
    vi.upsert("far", [0.0, 1.0], {})

    results = vi.query([1.0, 0.0], k=5, threshold=0.9)
    keys = [r.key for r in results]
    assert "near" in keys
    assert "far" not in keys


def test_drop_clears_all(tmp_path: Path) -> None:
    _, vi = _make(tmp_path)
    vi.upsert("x", [1.0, 0.0], {})
    vi.drop()
    results = vi.query([1.0, 0.0], k=5, threshold=0.0)
    assert results == []


def test_cosine_identical() -> None:
    assert abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-6


def test_cosine_orthogonal() -> None:
    assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-6


def test_cross_session_persistence(tmp_path: Path) -> None:
    """Two VectorIndex instances sharing the same json_path see the same data."""
    p = tmp_path / "state.json"
    s1 = Storage(redis_url=None, json_path=p)
    vi1 = VectorIndex(s1)
    vi1.upsert("key1", [1.0, 0.0], {"tag": "session1"})

    s2 = Storage(redis_url=None, json_path=p)
    vi2 = VectorIndex(s2)
    results = vi2.query([1.0, 0.0], k=1, threshold=0.0)
    assert len(results) == 1
    assert results[0].key == "key1"
