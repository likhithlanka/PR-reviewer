"""
ReviewForge MCP — Server entrypoint.

Registers all 9 tools and starts the MCP server over stdio transport.
Compatible with Claude Code, JAR, and any MCP-compatible client.
"""

import asyncio
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from tools.review_pr import ReviewPRTool
from tools.search import SearchTool
from tools.impact_analysis import ImpactAnalysisTool
from tools.read_file import ReadFileTool
from tools.fetch_doc import FetchDocTool
from tools.review_history import ReviewHistoryTool
from tools.pr_comments import PRCommentsTool
from tools.run_analysis import RunAnalysisTool
from tools.run_command import RunCommandTool

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("reviewforge")

app = Server("reviewforge-mcp")

# ── Tool instances ─────────────────────────────────────────────────────────
_tools = {
    "review_pr": ReviewPRTool(),
    "search": SearchTool(),
    "impact_analysis": ImpactAnalysisTool(),
    "read_file": ReadFileTool(),
    "fetch_doc": FetchDocTool(),
    "get_review_history": ReviewHistoryTool(),
    "get_pr_comments": PRCommentsTool(),
    "run_analysis": RunAnalysisTool(),
    "run_command": RunCommandTool(),
}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [t.definition() for t in _tools.values()]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name not in _tools:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        result = await _tools[name].run(arguments)
        return [TextContent(type="text", text=result)]
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return [TextContent(type="text", text=f"Error in {name}: {exc}")]


async def main() -> None:
    logger.info("ReviewForge MCP server starting…")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
