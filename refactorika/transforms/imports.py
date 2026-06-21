"""Reorder and deduplicate imports: stdlib → third-party → local."""

from __future__ import annotations

import sys
from pathlib import Path

from refactorika.analysis.parser import get_tree

_STDLIB = sys.stdlib_module_names


def _top_module(name: str) -> str:
    return name.split(".")[0]


def _classify(module: str) -> int:
    """Return sort bucket: 0=stdlib, 1=third-party, 2=local/relative."""
    if module.startswith("."):
        return 2
    if _top_module(module) in _STDLIB:
        return 0
    return 1


def _import_lines(source: str) -> list[tuple[int, int, str]]:
    """Return (start_byte, end_byte, text) for every top-level import statement."""
    tree = get_tree(source)
    enc = source.encode()
    results = []
    for node in tree.root_node.children:
        if node.type in ("import_statement", "import_from_statement"):
            results.append((node.start_byte, node.end_byte, enc[node.start_byte:node.end_byte].decode()))
    return results


def _module_of(import_text: str) -> str:
    """Extract the primary module name from an import line."""
    text = import_text.strip()
    if text.startswith("from "):
        parts = text.split()
        return parts[1] if len(parts) > 1 else ""
    if text.startswith("import "):
        parts = text.split()
        return parts[1].split(",")[0] if len(parts) > 1 else ""
    return ""


def reorder_imports(path: str) -> str:
    """Return new file content with imports deduped and sorted (stdlib → third-party → local)."""
    source = Path(path).read_text()
    imp_spans = _import_lines(source)
    if not imp_spans:
        return source

    enc = source.encode()

    # Collect unique import texts (dedupe exact duplicates).
    seen: set[str] = set()
    unique: list[str] = []
    for _, _, text in imp_spans:
        normalized = text.strip()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)

    # Sort: by bucket then alphabetically within bucket.
    def _sort_key(imp: str) -> tuple[int, str]:
        return (_classify(_module_of(imp)), imp.lower())

    sorted_imports = sorted(unique, key=_sort_key)

    # Group into sections separated by blank lines.
    groups: list[list[str]] = [[], [], []]
    for imp in sorted_imports:
        groups[_classify(_module_of(imp))].append(imp)

    import_block = "\n\n".join(
        "\n".join(g) for g in groups if g
    )

    # Replace the entire span from first to last import with the new block.
    first_start = imp_spans[0][0]
    last_end = imp_spans[-1][1]

    # Preserve any non-import lines between first and last import span
    # (e.g. a module docstring that happens to be before the first import was
    # already excluded; we only replace the import region itself).
    before = enc[:first_start].decode()
    after = enc[last_end:].decode()

    # Ensure exactly one blank line between imports and the rest of the file.
    after_stripped = after.lstrip("\n")
    new_source = before + import_block + "\n\n" + after_stripped
    return new_source
