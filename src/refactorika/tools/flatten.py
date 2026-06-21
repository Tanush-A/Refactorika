from mcp.server.fastmcp import FastMCP

router = FastMCP("flatten")


@router.tool()
def flatten_conditionals(file_path: str, max_nesting_depth: int = 3) -> str:
    """Flatten deeply nested conditionals using early returns and guard clauses.

    Returns the rewritten source with nesting depth reduced below max_nesting_depth.
    """
    raise NotImplementedError
