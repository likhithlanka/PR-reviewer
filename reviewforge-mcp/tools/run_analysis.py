"""
tools/run_analysis.py — Run static analysis on a file or repo.
MCP tool: run_analysis
"""

import json
import logging
from pathlib import Path

from mcp.types import Tool

import config

logger = logging.getLogger(__name__)


class RunAnalysisTool:
    def definition(self) -> Tool:
        return Tool(
            name="run_analysis",
            description=(
                "Run a specific static analyzer on a file or the full repository. "
                "Supported tools: ruff, mypy, bandit, hlint, semgrep."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "enum": ["ruff", "mypy", "bandit", "hlint", "semgrep"],
                        "description": "Which static analyzer to run.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Relative file path to analyze. Omit to run on the whole repo.",
                    },
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                },
                "required": ["tool_name", "workspace", "repo_slug"],
            },
        )

    async def run(self, args: dict) -> str:
        from pipeline.static_analyzer import StaticAnalyzer

        tool_name: str = args["tool_name"]
        file_path: str = args.get("file_path", "")
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]

        repo_path = config.REPOS_DIR / platform / workspace / repo_slug
        if not repo_path.exists():
            return "Repo not found locally. Run review_pr first."

        analyzer = StaticAnalyzer()
        results = analyzer.run_one(tool_name, repo_path, file_path)

        if not results:
            return f"No findings from {tool_name}."

        lines = [f"### Static Analysis: `{tool_name}`"]
        if file_path:
            lines[0] += f" on `{file_path}`"
        lines.append("")

        for item in results[:50]:  # Cap output
            sev = item.get("severity", "unknown").upper()
            msg = item.get("message", repr(item))
            f = item.get("file", "")
            ln = item.get("line", "")
            lines.append(f"- **[{sev}]** `{f}:{ln}` — {msg}")

        if len(results) > 50:
            lines.append(f"\n*(Showing 50 of {len(results)} findings)*")

        return "\n".join(lines)
