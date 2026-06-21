"""Tests for docs_gen: exports/dependents extraction, .md output, incremental flag."""

import os
from pathlib import Path

from refactorika.core.storage import Storage
from refactorika.docs_gen import generate_docs, get_context_map
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.context import ContextRetriever


SAMPLE_MODULE = '''\
"""Pricing utilities for the shop."""

from typing import Optional


def compute_price(qty: int, rate: float) -> float:
    """Return total price."""
    return qty * rate


def _internal_helper(x):
    return x * 2


class PriceTable:
    pass
'''


def _make_deps(tmp_path: Path):
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    mem = AgentMemory(storage)
    ret = ContextRetriever(storage, mem)
    return storage, mem, ret


def test_exports_extracted(tmp_path: Path) -> None:
    f = tmp_path / "pricing.py"
    f.write_text(SAMPLE_MODULE)
    storage, mem, ret = _make_deps(tmp_path)

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = generate_docs(str(f), storage, mem, ret)
    finally:
        os.chdir(orig_cwd)

    module = result["module"]
    export_names = {e["name"] for e in module["exports"]}
    # public functions and classes are exported
    assert "compute_price" in export_names
    assert "PriceTable" in export_names
    # private helper should NOT appear
    assert "_internal_helper" not in export_names


def test_purpose_hint_from_docstring(tmp_path: Path) -> None:
    f = tmp_path / "pricing.py"
    f.write_text(SAMPLE_MODULE)
    storage, mem, ret = _make_deps(tmp_path)

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = generate_docs(str(f), storage, mem, ret)
    finally:
        os.chdir(orig_cwd)

    assert "Pricing" in result["module"]["purpose_hint"]


def test_context_file_created(tmp_path: Path) -> None:
    f = tmp_path / "pricing.py"
    f.write_text(SAMPLE_MODULE)
    storage, mem, ret = _make_deps(tmp_path)

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        generate_docs(str(f), storage, mem, ret)
        ctx_file = tmp_path / ".refactorika" / "context"
        md_files = list(ctx_file.glob("*.md"))
    finally:
        os.chdir(orig_cwd)

    assert len(md_files) >= 1
    assert "compute_price" in md_files[0].read_text()


def test_incremental_flag_on_second_run(tmp_path: Path) -> None:
    f = tmp_path / "pricing.py"
    f.write_text(SAMPLE_MODULE)
    storage, mem, ret = _make_deps(tmp_path)

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        r1 = generate_docs(str(f), storage, mem, ret)
        r2 = generate_docs(str(f), storage, mem, ret)
    finally:
        os.chdir(orig_cwd)

    assert r1["incremental"] is False
    assert r2["incremental"] is True


def test_get_context_map_returns_persisted(tmp_path: Path) -> None:
    f = tmp_path / "pricing.py"
    f.write_text(SAMPLE_MODULE)
    storage, mem, ret = _make_deps(tmp_path)

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        generate_docs(str(f), storage, mem, ret)
        ctx_map = get_context_map(str(f), storage, mem, ret)
    finally:
        os.chdir(orig_cwd)

    assert "context" in ctx_map or "module" in ctx_map


SECOND_MODULE = '''\
"""Pricing helpers — tax and discount math."""


def apply_tax(total: float, rate: float) -> float:
    return total * (1 + rate)
'''


def _stub_embedder(text: str) -> list[float]:
    """Deterministic offline embedder: 26-dim lowercase-letter histogram."""
    vec = [0.0] * 26
    for ch in text.lower():
        idx = ord(ch) - ord("a")
        if 0 <= idx < 26:
            vec[idx] += 1.0
    # Ensure non-zero so cosine is defined.
    vec[0] += 0.1
    return vec


def test_relevant_returns_module(tmp_path: Path, monkeypatch) -> None:
    """C1: with a deterministic embedder, generate_docs upserts a module vector,
    so ContextRetriever.relevant() finds a related module (not the name-prefix
    fallback path)."""
    import refactorika.analysis.embeddings as emb

    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.setattr(emb, "embed_one", _stub_embedder)

    f1 = tmp_path / "pricing.py"
    f2 = tmp_path / "taxes.py"
    f1.write_text(SAMPLE_MODULE)
    f2.write_text(SECOND_MODULE)
    storage, mem, ret = _make_deps(tmp_path)

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        generate_docs(str(f1), storage, mem, ret)
        generate_docs(str(f2), storage, mem, ret)
        related = ret.relevant("pricing", k=3)
    finally:
        os.chdir(orig_cwd)

    # A module entry exists in the vector index, so relevant() returns one
    # (the other module), via the vector path — not the empty name-prefix fallback.
    assert any(r["module"] == "taxes" for r in related)


def test_last_updated_run_present_and_increments(tmp_path: Path) -> None:
    """C3: last_updated_run is set on persist and bumps on re-run."""
    f = tmp_path / "pricing.py"
    f.write_text(SAMPLE_MODULE)
    storage, mem, ret = _make_deps(tmp_path)

    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        r1 = generate_docs(str(f), storage, mem, ret)
        r2 = generate_docs(str(f), storage, mem, ret)
        ctx_map = get_context_map(str(f), storage, mem, ret)
    finally:
        os.chdir(orig_cwd)

    assert r1["module"]["last_updated_run"] == "run-1"
    assert r2["module"]["last_updated_run"] == "run-2"
    assert ctx_map["last_updated_run"] == "run-2"
