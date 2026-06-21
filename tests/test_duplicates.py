"""Tests for duplicate detection — structural tier + graceful tier-2 degradation."""

from pathlib import Path

from refactorika.analysis.duplicates import find_duplicates
from refactorika.core.storage import Storage
from refactorika.memory.vector_index import VectorIndex


def _make_storage(tmp_path: Path) -> Storage:
    return Storage(redis_url=None, json_path=tmp_path / "state.json")


def _make_vi(storage: Storage) -> VectorIndex:
    return VectorIndex(storage)


CLONE_A = """\
def process(x):
    if x > 0:
        return x * 2
    return 0
"""

CLONE_B = """\
def handle(y):
    if y > 0:
        return y * 2
    return 0
"""

DIFFERENT = """\
def compute(items):
    total = 0
    for item in items:
        total += item
    return total
"""


def test_structural_clone_detected(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text(CLONE_A)
    f2.write_text(CLONE_B)

    storage = _make_storage(tmp_path)
    vi = _make_vi(storage)

    result = find_duplicates(str(tmp_path), storage, vi)
    assert len(result["pairs"]) >= 1
    pair = result["pairs"][0]
    assert pair["match_type"] == "structural"
    assert pair["similarity"] == 1.0


def test_distinct_logic_not_flagged(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text(CLONE_A)
    f2.write_text(DIFFERENT)

    storage = _make_storage(tmp_path)
    vi = _make_vi(storage)

    result = find_duplicates(str(tmp_path), storage, vi)
    # CLONE_A and DIFFERENT have different AST shapes — no structural pair
    struct_pairs = [p for p in result["pairs"] if p["match_type"] == "structural"]
    assert len(struct_pairs) == 0


def test_tier1_only_when_embeddings_unavailable(tmp_path: Path, monkeypatch) -> None:
    """When embeddings are unavailable, result still has 'semantic' explanation."""
    import refactorika.analysis.embeddings as emb
    monkeypatch.setattr(emb, "available", lambda: False)

    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text(CLONE_A)
    f2.write_text(CLONE_B)

    storage = _make_storage(tmp_path)
    vi = _make_vi(storage)

    result = find_duplicates(str(tmp_path), storage, vi)
    assert "semantic" in result
    assert "unavailable" in result["semantic"]
    # Structural pairs still work
    assert any(p["match_type"] == "structural" for p in result["pairs"])


def test_structural_pairs_ranked_100_and_sorted(tmp_path: Path) -> None:
    """Structural pairs score round(similarity*100)=100, and results sort by rank desc."""
    # Two independent clone groups -> at least two structural pairs.
    (tmp_path / "a.py").write_text(CLONE_A)
    (tmp_path / "b.py").write_text(CLONE_B)

    storage = _make_storage(tmp_path)
    result = find_duplicates(str(tmp_path), storage, _make_vi(storage))

    struct = [p for p in result["pairs"] if p["match_type"] == "structural"]
    assert struct and all(p["rank"] == 100 for p in struct)
    ranks = [p["rank"] for p in result["pairs"]]
    assert ranks == sorted(ranks, reverse=True)  # sorted descending


def test_fingerprint_cached(tmp_path: Path) -> None:
    """Second call with same file should use cached fingerprints (no crash)."""
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text(CLONE_A)
    f2.write_text(CLONE_B)

    storage = _make_storage(tmp_path)
    vi = _make_vi(storage)

    r1 = find_duplicates(str(tmp_path), storage, vi)
    r2 = find_duplicates(str(tmp_path), storage, vi)
    assert len(r1["pairs"]) == len(r2["pairs"])
