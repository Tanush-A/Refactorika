from mcp.server.fastmcp import FastMCP

router = FastMCP("imports")


@router.tool()
def organize_imports(file_path: str) -> str:
    """Deduplicate and reorder imports: stdlib → third-party → local.

    Returns the full rewritten source with imports sorted and deduplicated.
    """
    raise NotImplementedError
