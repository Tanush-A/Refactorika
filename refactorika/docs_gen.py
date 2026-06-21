"""Living documentation: generate_docs + get_context_map."""

from __future__ import annotations

from pathlib import Path

from refactorika.analysis.parser import (
    get_tree,
    iter_functions,
    iter_imports,
    iter_symbols,
)
from refactorika.core.schema import ExportRef, ModuleContext
from refactorika.core.storage import Storage
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.context import ContextRetriever


# Patterns that flag non-obvious lines.
_FLAG_PATTERNS = [
    ("bare except:", "bare `except` (catches BaseException)"),
    ("getattr(", "dynamic attribute access via `getattr`"),
    ("# noqa", "`# noqa` suppression"),
]


def generate_docs(
    path: str,
    storage: Storage,
    agent_memory: AgentMemory,
    context_retriever: ContextRetriever,
) -> dict:
    """Extract module context, persist to agent memory, emit .md file."""
    p = Path(path).resolve()
    if not p.exists():
        return {"error": f"path not found: {path}"}

    source = p.read_text()
    tree = get_tree(source)
    module = _module_name(p)

    # Determine purpose hint from first docstring or dominant noun in function names.
    purpose_hint = _extract_purpose(source, tree)

    # Exports: top-level non-_ symbols.
    exports: list[ExportRef] = []
    for node, kind, name, line in iter_symbols(tree):
        if name.startswith("_"):
            continue
        sig = _extract_signature(node, source, kind)
        exports.append(ExportRef(name=name, kind=kind, signature=sig))

    # Dependents: modules that import this module (from stored contexts).
    dependents = context_retriever.dependents(module)

    # Flagged lines.
    flagged: list[str] = []
    lines = source.splitlines()
    for lineno, text in enumerate(lines, 1):
        for pattern, desc in _FLAG_PATTERNS:
            if pattern in text:
                flagged.append(f"line {lineno}: {desc}")
        # In-function imports (import inside a def body).
        stripped = text.strip()
        if stripped.startswith(("import ", "from ")) and _is_inside_function(lineno, tree):
            flagged.append(f"line {lineno}: import inside function body")
        # Magic number constants.
        import re  # noqa: PLC0415
        if re.search(r"\b\d{2,}\b", text) and "==" not in text and "line" not in text.lower():
            flagged.append(f"line {lineno}: possible magic number")

    # Retrieve prior context to detect changes.
    prior = agent_memory.get_context(module)
    changed_since_last: list[str] = []
    if prior is not None:
        prior_names = {e.name for e in prior.exports}
        curr_names = {e.name for e in exports}
        for name in curr_names - prior_names:
            changed_since_last.append(f"new export: `{name}`")
        for name in prior_names - curr_names:
            changed_since_last.append(f"removed export: `{name}`")
        for e in exports:
            prev = next((x for x in prior.exports if x.name == e.name), None)
            if prev and prev.signature != e.signature:
                changed_since_last.append(f"`{e.name}` signature changed")

    ctx = ModuleContext(
        path=str(p),
        purpose_hint=purpose_hint,
        exports=exports,
        dependents=dependents,
        flagged=list(dict.fromkeys(flagged)),  # dedupe, preserve order
        changed_since_last=changed_since_last,
        decisions=prior.decisions if prior else [],
    )

    # Persist.
    agent_memory.put_context(module, ctx)

    slug = module.replace("/", ".").removesuffix(".py")
    ctx_file = f".refactorika/context/{slug}.md"

    return {
        "path": str(p),
        "context_file": ctx_file,
        "persisted_to": "agent_memory" if storage.backend == "redis" else "json_fallback",
        "incremental": prior is not None,
        "module": ctx.to_dict(),
    }


def get_context_map(
    path: str,
    storage: Storage,
    agent_memory: AgentMemory,
    context_retriever: ContextRetriever,
) -> dict:
    """Return persisted context for a module without re-deriving."""
    p = Path(path).resolve()
    module = _module_name(p)

    ctx = agent_memory.get_context(module)
    source = "agent_memory" if storage.backend == "redis" else "json_fallback"

    if ctx is None:
        # Cold cache — derive on the fly.
        result = generate_docs(path, storage, agent_memory, context_retriever)
        result["source"] = "derived"
        return result

    related = context_retriever.relevant(module, k=3)
    return {
        "path": str(p),
        "source": source,
        "context": ctx.to_dict(),
        "related": related,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_name(p: Path) -> str:
    """Best-effort dotted module name from a file path."""
    try:
        rel = p.relative_to(Path.cwd())
        return str(rel).replace("/", ".").removesuffix(".py")
    except ValueError:
        pass
    # File is outside cwd — walk up to find the nearest package root (__init__.py).
    parts = list(p.parts)
    for i in range(len(parts) - 1, 0, -1):
        if not (Path(*parts[:i]) / "__init__.py").exists():
            # parts[i:] is the package-relative path
            segment = ".".join(parts[i:]).removesuffix(".py")
            return segment
    return p.stem


def _extract_purpose(source: str, tree) -> str:
    """Return first module-level docstring, or a noun phrase from function names."""
    root = tree.root_node
    for child in root.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type == "string" and sub.text:
                    raw = sub.text.decode().strip("'\"").strip()
                    return raw.splitlines()[0][:120]
    # Fallback: most common words in function names.
    names = [name for _, name, _ in iter_functions(tree)]
    if names:
        words: dict[str, int] = {}
        for n in names:
            import re  # noqa: PLC0415
            for w in re.split(r"[_A-Z]", n):
                if len(w) > 3:
                    words[w.lower()] = words.get(w.lower(), 0) + 1
        if words:
            top = max(words, key=words.__getitem__)
            return f"Module centered on `{top}` operations (inferred from function names)"
    return "<!-- claude: fill -->"


def _extract_signature(node, source: str, kind: str) -> str:
    if kind == "function":
        params = node.child_by_field_name("parameters")
        ret = node.child_by_field_name("return_type")
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode() if name_node and name_node.text else "?"
        param_text = params.text.decode() if params and params.text else "()"
        ret_text = f" -> {ret.text.decode()}" if ret and ret.text else ""
        return f"{name}{param_text}{ret_text}"
    if kind == "class":
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode() if name_node and name_node.text else "?"
        return f"class {name}"
    # assignment — grab the first line.
    start = node.start_byte
    line_end = source.encode().find(b"\n", start)
    snippet = source.encode()[start: line_end if line_end != -1 else start + 80].decode()
    return snippet[:80]


def _is_inside_function(lineno: int, tree) -> bool:
    """Heuristic: check if a line number falls inside any function node."""
    def _walk(node) -> bool:
        if node.type == "function_definition":
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            if start <= lineno <= end:
                return True
        return any(_walk(child) for child in node.children)

    return _walk(tree.root_node)
