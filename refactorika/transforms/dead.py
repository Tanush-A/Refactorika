"""Remove high-confidence dead symbols from a source file using tree-sitter byte offsets."""

from __future__ import annotations

from pathlib import Path

from refactorika.analysis.parser import get_tree


def remove_dead_symbols(path: str, names_to_remove: set[str]) -> str:
    """Return new file content with named top-level symbols excised.

    Only removes symbols whose *unqualified* name is in names_to_remove.
    Preserves all other code exactly. Safe to call with an empty set (returns source unchanged).
    """
    source = Path(path).read_text()
    if not names_to_remove:
        return source

    tree = get_tree(source)
    enc = source.encode()
    root = tree.root_node

    # Collect (start_byte, end_byte) for each top-level node to remove.
    # Also strip any decorator nodes that immediately precede the definition.
    removals: list[tuple[int, int]] = []

    children = list(root.children)
    for i, node in enumerate(children):
        name = _symbol_name(node)
        if name not in names_to_remove:
            continue

        # Include leading decorators (they appear as sibling nodes just before).
        start = node.start_byte
        for j in range(i - 1, -1, -1):
            prev = children[j]
            if prev.type == "decorator":
                start = prev.start_byte
            else:
                break

        end = node.end_byte
        removals.append((start, end))

    if not removals:
        return source

    # Build new source by skipping removed byte ranges.
    # Merge overlapping/adjacent spans just in case.
    removals.sort()
    merged: list[tuple[int, int]] = []
    for s, e in removals:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    parts: list[bytes] = []
    cursor = 0
    for s, e in merged:
        parts.append(enc[cursor:s])
        cursor = e

        # Consume the trailing newline(s) after the removed block so we
        # don't leave a double blank line.
        while cursor < len(enc) and enc[cursor:cursor+1] == b"\n":
            cursor += 1
        parts.append(b"\n")  # keep one blank line as separator

    parts.append(enc[cursor:])
    result = b"".join(parts).decode()

    # Clean up sequences of more than two consecutive blank lines.
    import re  # noqa: PLC0415
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def _symbol_name(node) -> str | None:
    """Return the unqualified name of a top-level definition node, or None."""
    if node.type in ("function_definition", "class_definition"):
        name_node = node.child_by_field_name("name")
        if name_node and name_node.text:
            return name_node.text.decode()
    if node.type in ("expression_statement", "assignment"):
        for child in node.children:
            if child.type == "identifier" and child.text:
                return child.text.decode()
            if child.type == "assignment":
                left = child.child_by_field_name("left")
                if left and left.type == "identifier" and left.text:
                    return left.text.decode()
    return None
