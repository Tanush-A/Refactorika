"""Shared tree-sitter front end — one walker used by all analysis modules."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

_PY_LANGUAGE = Language(tspython.language())
_PARSER = Parser(_PY_LANGUAGE)


def get_tree(source: str) -> Any:
    return _PARSER.parse(source.encode())


def iter_functions(tree: Any) -> Iterator[tuple[Node, str, int]]:
    """Yield (node, name, start_line) for every function_definition in the tree."""
    def _walk(node: Node) -> Iterator[tuple[Node, str, int]]:
        if node.type == "function_definition":
            name = _child_text(node, "name") or "<anon>"
            yield node, name, node.start_point[0] + 1
        for child in node.children:
            yield from _walk(child)

    yield from _walk(tree.root_node)


def iter_symbols(tree: Any) -> Iterator[tuple[Node, str, str, int]]:
    """Yield (node, kind, name, start_line) for functions, classes, and module-level assignments."""
    root = tree.root_node
    for node in root.children:
        if node.type == "function_definition":
            name = _child_text(node, "name") or "<anon>"
            yield node, "function", name, node.start_point[0] + 1
        elif node.type == "class_definition":
            name = _child_text(node, "name") or "<anon>"
            yield node, "class", name, node.start_point[0] + 1
        elif node.type in ("expression_statement", "assignment"):
            name = _assignment_name(node)
            if name:
                yield node, "assignment", name, node.start_point[0] + 1


def iter_calls(tree: Any) -> Iterator[str]:
    """Yield call target names from the tree (best-effort; skips complex expressions)."""
    def _walk(node: Node) -> Iterator[str]:
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is not None:
                if fn.type == "identifier":
                    yield fn.text.decode() if fn.text else ""
                elif fn.type == "attribute":
                    attr = fn.child_by_field_name("attribute")
                    if attr is not None and attr.text:
                        yield attr.text.decode()
        for child in node.children:
            yield from _walk(child)

    yield from _walk(tree.root_node)


def iter_imports(tree: Any) -> Iterator[tuple[str, list[str]]]:
    """Yield (module, [names]) for import and from-import statements."""
    def _walk(node: Node) -> Iterator[tuple[str, list[str]]]:
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name" and child.text:
                    yield child.text.decode(), []
        elif node.type == "import_from_statement":
            mod_node = node.child_by_field_name("module_name")
            mod = mod_node.text.decode() if mod_node and mod_node.text else ""
            names: list[str] = []
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import", "identifier"):
                    if child != mod_node and child.text:
                        names.append(child.text.decode().split(" as ")[0])
            yield mod, names
        for child in node.children:
            yield from _walk(child)

    yield from _walk(tree.root_node)


def function_text(node: Node, source: str) -> str:
    """Return the full source text of a function node (signature + body)."""
    start = node.start_byte
    end = node.end_byte
    return source.encode()[start:end].decode()


_NESTING_NODES = frozenset({
    "if_statement", "for_statement", "while_statement", "with_statement",
    "try_statement", "match_statement",
})


def max_nesting_depth(node: Node) -> int:
    """Deepest nesting of control-flow blocks inside a function (signature itself = 0).

    A long-but-flat function and a short-but-deeply-nested one are both god-function
    shapes; line count alone sees neither. This captures the nesting axis.
    """

    def _walk(n: Node, depth: int) -> int:
        deepest = depth
        for child in n.children:
            child_depth = depth + 1 if child.type in _NESTING_NODES else depth
            deepest = max(deepest, _walk(child, child_depth))
        return deepest

    return _walk(node, 0)


def canonical_type_stream(node: Node) -> list[str]:
    """Return node types from a subtree, replacing identifiers/literals with placeholders."""
    result: list[str] = []

    def _walk(n: Node) -> None:
        if n.type in ("identifier", "string", "integer", "float", "true", "false", "none"):
            result.append("ID" if n.type == "identifier" else "LIT")
        else:
            result.append(n.type)
        for child in n.children:
            _walk(child)

    _walk(node)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _child_text(node: Node, field: str) -> str | None:
    child = node.child_by_field_name(field)
    if child and child.text:
        return child.text.decode()
    return None


def _assignment_name(node: Node) -> str | None:
    for child in node.children:
        if child.type == "identifier" and child.text:
            return child.text.decode()
        if child.type == "assignment":
            left = child.child_by_field_name("left")
            if left and left.type == "identifier" and left.text:
                return left.text.decode()
    return None
