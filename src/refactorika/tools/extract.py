from mcp.server.fastmcp import FastMCP

router = FastMCP("extract")


@router.tool()
def extract_helpers(file_path: str, max_function_lines: int = 40) -> str:
    """Extract reusable helpers from functions that exceed max_function_lines.

    Returns the rewritten source with extracted helper functions added above
    their call sites.
    """
    raise NotImplementedError
