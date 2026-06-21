# Architecture

## Project Layout

```
Refactorika/
├── pyproject.toml                        # package metadata + dependencies
├── src/
│   └── refactorika/
│       ├── __init__.py
│       ├── server.py                     # FastMCP instance + entry point
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── split_file.py             # tool: split large file into modules
│       │   ├── imports.py                # tool: deduplicate + reorder imports
│       │   ├── extract.py                # tool: extract helpers from long functions
│       │   └── flatten.py                # tool: flatten nested conditionals
│       ├── analysis/
│       │   ├── __init__.py
│       │   ├── parser.py                 # tree-sitter AST parsing
│       │   └── metrics.py                # complexity scoring (nesting depth, fn length)
│       ├── transforms/
│       │   ├── __init__.py
│       │   └── rewrite.py                # apply AST edits + serialize back to source
│       └── cache.py                      # Redis client + result caching helpers
└── tests/
    ├── __init__.py
    ├── test_imports.py
    ├── test_extract.py
    ├── test_flatten.py
    └── test_split_file.py
```

## MCP Framework

Refactorika uses the **`mcp` Python SDK** with `FastMCP` — a decorator-based API similar to FastAPI.

**Entry point** (`src/refactorika/server.py`):
```python
from mcp.server.fastmcp import FastMCP
from refactorika.tools import split_file, imports, extract, flatten

mcp = FastMCP("refactorika")
```

**Defining a tool** (each file under `tools/`):
```python
from mcp.server.fastmcp import FastMCP

router = FastMCP("imports")

@router.tool()
def organize_imports(file_path: str) -> str:
    """Deduplicates and reorders imports: stdlib → third-party → local."""
    ...
```

Claude calls tools by name during a conversation. All parameters and return values must be JSON-serializable. Return a string with the refactored source or a structured result.

## Data Flow

```
Claude invokes tool
    └── server.py (FastMCP)
            └── tools/*.py          ← validate input, call analysis + transforms
                    ├── analysis/parser.py     ← parse source to AST (tree-sitter)
                    ├── analysis/metrics.py    ← score complexity
                    ├── transforms/rewrite.py  ← apply edits, serialize to source
                    └── cache.py               ← cache result in Redis by file hash
```

## Layer Responsibilities

| Layer | Files | Responsibility |
|---|---|---|
| MCP server | `server.py` | Registers tools with FastMCP; entry point for `mcp dev` |
| Tools | `tools/*.py` | One file per refactoring capability; thin orchestration |
| Analysis | `analysis/parser.py`, `analysis/metrics.py` | Read-only: parse + score source, never write |
| Transforms | `transforms/rewrite.py` | Write-only: take an AST + edit spec, return new source |
| Cache | `cache.py` | Redis-backed memoization; keyed by file content hash |

## Running the Server

```bash
# Development (MCP inspector)
mcp dev src/refactorika/server.py

# Production (stdio transport for Claude Desktop / Claude Code)
python -m refactorika.server
```

## Testing

```bash
pytest tests/           # run all tests
pyright src/            # type check
ruff check src/         # lint
```
