"""Impact / related-code retrieval: semantic neighbours + structural dependents."""

from pathlib import Path

from refactorika.analysis.related import find_related
from refactorika.core.storage import Storage
from refactorika.memory.vector_index import VectorIndex


def _make(tmp_path: Path):
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    return storage, VectorIndex(storage)


def test_find_related_semantic_and_dependents(tmp_path: Path, monkeypatch) -> None:
    import refactorika.analysis.embeddings as emb

    # Stub: INVOICE-tagged functions cluster together; everything else is far.
    def _embed_one(text: str) -> list[float]:
        return [1.0, 0.0] if "INVOICE" in text else [0.0, 1.0]

    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.setattr(emb, "embed_one", _embed_one)
    monkeypatch.setattr(emb, "embed", lambda ts: [_embed_one(t) for t in ts])

    # pricing (target) and billing share logic (different shape); greet is unrelated.
    (tmp_path / "pricing.py").write_text(
        "def compute_price(items):\n"
        "    # INVOICE\n"
        "    t = 0\n"
        "    for i in items:\n"
        "        t = t + i\n"
        "    return t\n"
    )
    (tmp_path / "billing.py").write_text(
        "def calc_invoice(values):\n    # INVOICE\n    return sum(values)\n"
    )
    (tmp_path / "greet.py").write_text('def greet(name):\n    return f"hi {name}"\n')
    # checkout imports + calls pricing -> a structural dependent.
    (tmp_path / "checkout.py").write_text(
        "from pricing import compute_price\n\ndef go():\n    return compute_price([])\n"
    )

    storage, vi = _make(tmp_path)
    result = find_related(str(tmp_path / "pricing.py"), storage, vi, k=5)

    # SEMANTIC: billing.calc_invoice surfaces; greet and the target's own fn do not.
    related_names = {r["name"] for r in result["related"]}
    assert "calc_invoice" in related_names
    assert "greet" not in related_names
    assert "compute_price" not in related_names  # same file excluded
    top = result["related"][0]
    assert top["name"] == "calc_invoice"
    assert top["similarity"] >= 0.9
    assert top["similar_to"] == "compute_price"

    # STRUCTURAL: checkout depends on pricing.
    assert "checkout" in result["dependents"]


def test_find_related_no_embeddings_still_returns_dependents(tmp_path: Path) -> None:
    # conftest disables embeddings by default -> related empty, but dependents work.
    (tmp_path / "pricing.py").write_text("def compute_price(x):\n    return x\n")
    (tmp_path / "checkout.py").write_text(
        "from pricing import compute_price\n\ndef go():\n    return compute_price(1)\n"
    )
    storage, vi = _make(tmp_path)
    result = find_related(str(tmp_path / "pricing.py"), storage, vi, k=5)
    assert result["related"] == []
    assert "related_note" in result
    assert "checkout" in result["dependents"]
