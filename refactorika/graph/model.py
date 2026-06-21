"""The program graph: symbols (nodes) and reference edges, with serialization.

A `Symbol` is one addressable thing in the repo — a module, function, method, or
class — keyed by its module-qualified name (`orders.compute_total`,
`billing.Invoice.total`). An edge `A -> B` means *A references B* (A calls/uses B),
so B is a dependency of A. Leaves (no outgoing edges) depend on nothing inside the
repo and are refactored first; reversing the edges answers "who depends on B."

The graph is built by `resolver.py` from real static analysis. This module is pure
data + traversal helpers so it can be persisted to Redis (`to_dict`/`from_dict`) and
reasoned about without re-running resolution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Symbol kinds. "module" is the file-level node that owns top-level code.
KIND_MODULE = "module"
KIND_FUNCTION = "function"
KIND_METHOD = "method"
KIND_CLASS = "class"


@dataclass
class Symbol:
    """One node in the program graph."""

    qualname: str
    name: str
    kind: str
    file: str
    line: int
    column: int = 0
    scope: str | None = None  # enclosing symbol's qualname (None at module level)
    is_private: bool = False  # name starts with a single/double underscore
    is_exported: bool = False  # in __all__, or public and module-level
    decorators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Symbol":
        return cls(**d)


@dataclass
class Graph:
    """The whole-program reference graph."""

    symbols: dict[str, Symbol] = field(default_factory=dict)
    # reference edges: src qualname -> set of dst qualnames it references
    edges: dict[str, set[str]] = field(default_factory=dict)
    # module-level import edges: module -> set of imported repo modules
    import_edges: dict[str, set[str]] = field(default_factory=dict)
    # reachability anchors (public API, __all__, __main__, tests, registrations)
    entry_points: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ build
    def add_symbol(self, sym: Symbol) -> None:
        self.symbols[sym.qualname] = sym
        self.edges.setdefault(sym.qualname, set())

    def add_edge(self, src: str, dst: str) -> None:
        """Record that *src* references *dst* (dst is a dependency of src)."""
        if src == dst:
            return  # ignore self-reference (recursion) for ordering purposes
        self.edges.setdefault(src, set()).add(dst)

    def add_import_edge(self, src_module: str, dst_module: str) -> None:
        if src_module != dst_module:
            self.import_edges.setdefault(src_module, set()).add(dst_module)

    def add_entry_point(self, qualname: str) -> None:
        self.entry_points.add(qualname)

    # --------------------------------------------------------------- traverse
    def outgoing(self, qualname: str) -> set[str]:
        """Dependencies of *qualname* (symbols it references), within the graph."""
        return {d for d in self.edges.get(qualname, set()) if d in self.symbols}

    def incoming(self, qualname: str) -> set[str]:
        """Dependents of *qualname* (symbols that reference it)."""
        return {src for src, dsts in self.edges.items() if qualname in dsts and src in self.symbols}

    def reverse_edges(self) -> dict[str, set[str]]:
        """dst -> set of srcs that reference it (only edges within the graph)."""
        rev: dict[str, set[str]] = {q: set() for q in self.symbols}
        for src, dsts in self.edges.items():
            if src not in self.symbols:
                continue
            for dst in dsts:
                if dst in self.symbols:
                    rev[dst].add(src)
        return rev

    def call_sites(self, qualname: str) -> int:
        """How many distinct symbols reference *qualname*."""
        return len(self.incoming(qualname))

    # ------------------------------------------------------------ serialization
    def to_dict(self) -> dict:
        return {
            "symbols": {q: s.to_dict() for q, s in self.symbols.items()},
            "edges": {q: sorted(d) for q, d in self.edges.items()},
            "import_edges": {q: sorted(d) for q, d in self.import_edges.items()},
            "entry_points": sorted(self.entry_points),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Graph":
        g = cls()
        g.symbols = {q: Symbol.from_dict(s) for q, s in d.get("symbols", {}).items()}
        g.edges = {q: set(v) for q, v in d.get("edges", {}).items()}
        g.import_edges = {q: set(v) for q, v in d.get("import_edges", {}).items()}
        g.entry_points = set(d.get("entry_points", []))
        for q in g.symbols:
            g.edges.setdefault(q, set())
        return g
