"""Language adapter registry — import this package to get detect_language."""

from .generic_adapter import GenericAdapter
from .python_adapter import PythonAdapter
from .registry import detect_language, register_adapter

register_adapter(PythonAdapter())
register_adapter(GenericAdapter(), generic=True)

# Optional TypeScript support (requires tree-sitter-typescript).
try:
    from .typescript_adapter import TypeScriptAdapter  # type: ignore[import]

    register_adapter(TypeScriptAdapter())
except ImportError:
    pass

__all__ = ["detect_language", "register_adapter"]
