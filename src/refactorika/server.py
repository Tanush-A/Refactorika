from mcp.server.fastmcp import FastMCP

from refactorika.tools import extract, flatten, imports, split_file

mcp = FastMCP("refactorika")

mcp.mount("split_file", split_file.router)
mcp.mount("imports", imports.router)
mcp.mount("extract", extract.router)
mcp.mount("flatten", flatten.router)

if __name__ == "__main__":
    mcp.run()
