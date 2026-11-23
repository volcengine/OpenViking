# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==1.27.0"]
# ///
"""Serve a fictional project's current status as a read-only MCP tool."""

import argparse
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP(
    "live-context-demo",
    host="127.0.0.1",
    port=8765,
    stateless_http=True,
    json_response=True,
)
status_file = Path(__file__).with_name("status.json")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def project_status(project: str) -> str:
    """Read the current pending feedback count for the Lighthouse demo project."""
    if project != "Lighthouse":
        raise ValueError("Unknown demo project; use Lighthouse.")
    status = json.loads(status_file.read_text(encoding="utf-8"))
    return json.dumps({"project": project, **status}, ensure_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Local MCP port (default: 8765)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")
