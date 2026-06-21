from tree_sitter import Node

from refactorika.analysis.parser import parse

_NESTING_NODES = {"if_statement", "for_statement", "while_statement", "with_statement", "try_statement"}


def max_nesting_depth(source: str) -> int:
    """Return the maximum conditional/loop nesting depth in the source."""
    root = parse(source)
    return _depth(root, 0)


def function_line_counts(source: str) -> dict[str, int]:
    """Return a mapping of function name → line count for every function in source."""
    root = parse(source)
    results: dict[str, int] = {}
    _collect_functions(root, source, results)
    return results


def _depth(node: Node, current: int) -> int:
    if node.type in _NESTING_NODES:
        current += 1
    return max((current, *(_depth(child, current) for child in node.children)))


def _collect_functions(node: Node, source: str, out: dict[str, int]) -> None:
    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        if name_node:
            name = source[name_node.start_byte:name_node.end_byte]
            start_line = node.start_point[0]
            end_line = node.end_point[0]
            out[name] = end_line - start_line + 1
    for child in node.children:
        _collect_functions(child, source, out)
