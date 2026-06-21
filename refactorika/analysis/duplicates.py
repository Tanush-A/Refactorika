"""Duplicate function detection — structural (AST fingerprint) + semantic (embeddings).

Public entry point:
    find_duplicates(path, storage, vector_index, threshold=0.83) -> dict

Tier 1 (structural, cheap):
  - canonical_type_stream(node) -> join -> sha1
  - Groups by sha1; ≥2 functions per group = structural clones (similarity=1.0)

Tier 2 (semantic, requires embeddings provider):
  - embed each function's source text, upsert to vector_index
  - query for near-duplicates at threshold
  - skip pairs already covered by tier-1

Returns:
  {
    "path": "...",
    "pairs": [DuplicatePair.to_dict(), ...],
    "semantic": "unavailable — ..."   # only when tier-2 skipped
  }
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from refactorika.analysis.parser import (
    canonical_type_stream,
    function_text,
    get_tree,
    iter_functions,
    iter_imports,
)
from refactorika.core.schema import DuplicatePair, SymbolRef
from refactorika.memory.vector_index import _cosine

if TYPE_CHECKING:
    from refactorika.core.storage import Storage
    from refactorika.memory.vector_index import VectorIndex

_SKIP_DIRS = {".venv", "__pycache__", "tests"}


def _collect_py_files(path: str) -> list[Path]:
    """Return all .py files under path, skipping unwanted directories."""
    p = Path(path)
    if p.is_file():
        return [p] if p.suffix == ".py" else []

    result: list[Path] = []
    for child in p.rglob("*.py"):
        # Skip if any path component is in _SKIP_DIRS
        if any(part in _SKIP_DIRS for part in child.parts):
            continue
        result.append(child)
    return result


def _sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def _count_calls_in_source(source: str, fn_name: str) -> int:
    """Approximate: count occurrences of 'fn_name(' in source."""
    return source.count(f"{fn_name}(")


def _count_imports(source: str) -> int:
    """Count import statements as a proxy for 'centrality'."""
    try:
        tree = get_tree(source)
        return sum(1 for _ in iter_imports(tree))
    except Exception:
        return 0


def _pick_consolidation_target(
    a_ref: SymbolRef,
    b_ref: SymbolRef,
    sources: dict[str, str],
) -> tuple[SymbolRef, str]:
    """
    Pick the canonical copy to keep.
    Prefer the function with more in-file call-site occurrences.
    On tie, prefer the file with more import statements (more "central").
    Returns (target_ref, reason).
    """
    src_a = sources.get(a_ref.file, "")
    src_b = sources.get(b_ref.file, "")

    calls_a = _count_calls_in_source(src_a, a_ref.name)
    calls_b = _count_calls_in_source(src_b, b_ref.name)

    if calls_a > calls_b:
        return a_ref, f"{a_ref.name} has more call sites ({calls_a} vs {calls_b})"
    if calls_b > calls_a:
        return b_ref, f"{b_ref.name} has more call sites ({calls_b} vs {calls_a})"

    # Tie-break on import count (more imports = more central)
    imports_a = _count_imports(src_a)
    imports_b = _count_imports(src_b)

    if imports_a >= imports_b:
        return a_ref, f"tie on call sites; {a_ref.file} has more imports ({imports_a})"
    return b_ref, f"tie on call sites; {b_ref.file} has more imports ({imports_b})"


def find_duplicates(
    path: str,
    storage: "Storage",
    vector_index: "VectorIndex",
    threshold: float = 0.83,
) -> dict:
    """Detect duplicate functions in path (file or directory).

    Returns a dict with keys:
      "path"   — the input path
      "pairs"  — list of DuplicatePair.to_dict()
      "semantic" (optional) — skipped-reason string when tier-2 unavailable
    """
    from refactorika.analysis import embeddings

    py_files = _collect_py_files(path)
    if not py_files:
        return {"path": path, "pairs": []}

    # ------------------------------------------------------------------
    # Collect all functions
    # ------------------------------------------------------------------
    # List of (file_path_str, name, line, node, source)
    all_funcs: list[tuple[str, str, int, object, str]] = []
    sources: dict[str, str] = {}

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sources[str(py_file)] = source
        try:
            tree = get_tree(source)
        except Exception:
            continue
        for node, name, line in iter_functions(tree):
            all_funcs.append((str(py_file), name, line, node, source))

    if not all_funcs:
        return {"path": path, "pairs": []}

    # ------------------------------------------------------------------
    # Tier 1: Structural fingerprint (SHA1 of canonical type stream)
    # ------------------------------------------------------------------
    fingerprint_map: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    # Structural fingerprint per "file:name" key, threaded into tier-2 vector meta.
    fingerprint_by_key: dict[str, str] = {}

    for file_str, name, line, node, source in all_funcs:
        cached = storage.cache_get(f"fp:{file_str}:{name}")
        if cached and isinstance(cached, dict):
            sha1 = cached.get("sha1", "")
        else:
            type_stream = canonical_type_stream(node)  # type: ignore[arg-type]
            sha1 = _sha1_hex(" ".join(type_stream))
            storage.cache_set(f"fp:{file_str}:{name}", {"sha1": sha1})

        fingerprint_by_key[f"{file_str}:{name}"] = sha1
        if sha1:
            fingerprint_map[sha1].append((file_str, name, line))

    structural_pairs: list[DuplicatePair] = []
    # Track exact structural pairs already emitted, to dedupe against tier 2.
    structural_pair_keys: set[frozenset] = set()

    for sha1, members in fingerprint_map.items():
        if len(members) < 2:
            continue
        # Emit all pairs within the group
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                fa, na, la = members[i]
                fb, nb, lb = members[j]

                ref_a = SymbolRef(file=fa, name=na, line=la)
                ref_b = SymbolRef(file=fb, name=nb, line=lb)

                target, reason = _pick_consolidation_target(ref_a, ref_b, sources)

                structural_pairs.append(
                    DuplicatePair(
                        a=ref_a,
                        b=ref_b,
                        similarity=1.0,
                        match_type="structural",
                        consolidation_target=target,
                        reason=reason,
                        rank=100,  # round(1.0 * 100)
                    )
                )
                structural_pair_keys.add(frozenset({f"{fa}:{na}", f"{fb}:{nb}"}))

    # ------------------------------------------------------------------
    # Tier 2: Semantic similarity via embeddings
    # ------------------------------------------------------------------
    structural_pairs.sort(key=lambda p: p.rank, reverse=True)
    result: dict = {
        "path": path,
        "pairs": [p.to_dict() for p in structural_pairs],
    }

    if not embeddings.available():
        result["semantic"] = (
            "unavailable — install refactorika[semantic] "
            "(sentence-transformers or openai)"
        )
        return result

    # Embed each function ONCE (batched: one API round-trip), then upsert.
    # embedded[key] = {"vec", "text", "file", "name", "line"}
    embedded: dict[str, dict] = {}
    embed_texts: list[str] = []
    embed_keys: list[str] = []
    embed_meta: list[tuple[str, str, int]] = []

    for file_str, name, line, node, source in all_funcs:
        try:
            text = function_text(node, source)  # type: ignore[arg-type]
        except Exception:
            continue
        key = f"{file_str}:{name}"
        embed_texts.append(text)
        embed_keys.append(key)
        embed_meta.append((file_str, name, line))

    try:
        vecs = embeddings.embed(embed_texts)
    except Exception:
        vecs = []

    for key, text, (file_str, name, line), vec in zip(
        embed_keys, embed_texts, embed_meta, vecs
    ):
        if vec is None:
            continue
        embedded[key] = {
            "vec": vec,
            "text": text,
            "file": file_str,
            "name": name,
            "line": line,
        }
        vector_index.upsert(
            key,
            vec,
            meta={
                "file": file_str,
                "name": name,
                "line": line,
                "fingerprint": fingerprint_by_key.get(key, ""),
            },
            text=text,
        )

    # Query for near-duplicate pairs (semantic) via hybrid search.
    semantic_pairs: list[DuplicatePair] = []
    # Track dedupe: frozenset of two keys already emitted as a semantic pair
    seen_semantic: set[frozenset] = set()

    for query_key, entry in embedded.items():
        vec = entry["vec"]
        text = entry["text"]
        file_str = entry["file"]
        name = entry["name"]
        line = entry["line"]

        try:
            neighbors = vector_index.query_hybrid(vec, text, k=5)
        except Exception:
            continue

        for neighbor in neighbors:
            if neighbor.key == query_key:
                continue  # skip self

            pair_key: frozenset = frozenset([query_key, neighbor.key])
            if pair_key in seen_semantic:
                continue

            # Skip only if this exact pair was already emitted in tier 1.
            if frozenset({query_key, neighbor.key}) in structural_pair_keys:
                continue

            # Recompute TRUE cosine — RRF/hybrid scores are not in [0,1].
            neighbor_entry = embedded.get(neighbor.key)
            if neighbor_entry is None:
                continue
            cosine = _cosine(vec, neighbor_entry["vec"])
            if cosine < threshold:
                continue

            seen_semantic.add(pair_key)

            # Parse neighbor key
            n_meta = neighbor.meta
            n_file = n_meta.get("file", "")
            n_name = n_meta.get("name", "")
            n_line = n_meta.get("line", 0)

            ref_a = SymbolRef(file=file_str, name=name, line=line)
            ref_b = SymbolRef(file=n_file, name=n_name, line=n_line)

            target, reason = _pick_consolidation_target(ref_a, ref_b, sources)
            similarity = round(cosine, 4)

            semantic_pairs.append(
                DuplicatePair(
                    a=ref_a,
                    b=ref_b,
                    similarity=similarity,
                    match_type="semantic",
                    consolidation_target=target,
                    reason=reason,
                    rank=round(cosine * 100),
                )
            )

    all_pairs = structural_pairs + semantic_pairs
    all_pairs.sort(key=lambda p: p.rank, reverse=True)
    result["pairs"] = [p.to_dict() for p in all_pairs]
    return result
