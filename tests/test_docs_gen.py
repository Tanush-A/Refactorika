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
