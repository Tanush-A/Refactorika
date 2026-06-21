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


def test_semantic_duplicate_detected(tmp_path: Path, monkeypatch) -> None:
    """Tier-2 semantic detection with a deterministic stub embedder (no network).

    Two functions are semantically equivalent but structurally different (for-loop
    accumulator vs sum()), so tier-1 does NOT claim them. A shared "INVOICE" marker
    in their bodies maps both to the same stub vector; a third unrelated function
    maps to a far vector. This local monkeypatch overrides conftest's autouse
    `available -> False` fixture (test-local patch wins).
    """
    import refactorika.analysis.embeddings as emb

    def _embed_one(text: str) -> list[float]:
        # Twins share the "INVOICE" marker -> identical vector; everything else far.
        return [1.0, 0.0] if "INVOICE" in text else [0.0, 1.0]

    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.setattr(emb, "embed_one", _embed_one)
    monkeypatch.setattr(emb, "embed", lambda texts: [_embed_one(t) for t in texts])
    monkeypatch.setattr(emb, "provider_dim", lambda: ("stub", 2))

    # Semantically equivalent, structurally different -> no tier-1 pair.
    (tmp_path / "a.py").write_text(
        "def compute_total(items):\n"
        "    # INVOICE\n"
        "    t = 0\n"
        "    for i in items:\n"
        "        t = t + i\n"
        "    return t\n"
    )
    (tmp_path / "b.py").write_text(
        "def sum_invoice(values):\n"
        "    # INVOICE\n"
        "    return sum(values)\n"
    )
    # Distinct, no marker -> far vector, must not pair with anything.
    (tmp_path / "c.py").write_text('def greet(name):\n    return f"hi {name}"\n')

    storage = _make_storage(tmp_path)
    vi = _make_vi(storage)

    result = find_duplicates(str(tmp_path), storage, vi, threshold=0.9)

    semantic = [p for p in result["pairs"] if p["match_type"] == "semantic"]
    assert semantic, "expected a semantic duplicate pair"

    invoice_names = {"compute_total", "sum_invoice"}
    invoice_pairs = [
        p
        for p in semantic
        if {p["a"]["name"], p["b"]["name"]} == invoice_names
    ]
    assert invoice_pairs, "expected the two INVOICE functions to be paired semantically"
    assert invoice_pairs[0]["similarity"] >= 0.9

    # greet must never appear in any pair (semantic or structural).
    for p in result["pairs"]:
        assert "greet" not in {p["a"]["name"], p["b"]["name"]}


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
