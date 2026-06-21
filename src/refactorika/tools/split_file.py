from mcp.server.fastmcp import FastMCP

router = FastMCP("split_file")


@router.tool()
def split_file(file_path: str, max_lines: int = 200) -> str:
    """Split a large Python file into logically grouped modules.

    Returns a JSON description of the proposed split: which functions/classes
    go into which new module, with suggested file names.
    """
    raise NotImplementedError
