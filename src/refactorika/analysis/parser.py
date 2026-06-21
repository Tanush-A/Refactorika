import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

PY_LANGUAGE = Language(tspython.language())


def parse(source: str) -> Node:
    """Parse Python source into a tree-sitter AST root node."""
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source.encode())
    return tree.root_node
