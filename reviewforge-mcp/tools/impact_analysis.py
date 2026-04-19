"""
tools/impact_analysis.py — MCP tool for interactive impact analysis.
MCP tool: impact_analysis
"""

import json
import logging
from pathlib import Path

from mcp.types import Tool

from pipeline.cache import session

logger = logging.getLogger(__name__)


class ImpactAnalysisTool:
    def definition(self) -> Tool:
        return Tool(
            name="impact_analysis",
            description=(
                "Given a function name or file path, returns: who calls it (callers), "
                "what it calls (callees), and which callers did NOT change in the current PR "
                "(potential breakage points). Uses the AST code graph built during review_pr."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Function name OR file path to analyse (e.g. 'processPayment' or 'src/payment.py').",
                    },
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                },
                "required": ["entity", "workspace", "repo_slug"],
            },
        )

    async def run(self, args: dict) -> str:
        from pipeline.impact_analyzer import ImpactAnalyzer

        entity: str = args["entity"]
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]

        # Load graph from session cache
        graph_data = session.get(f"graph:{platform}:{workspace}:{repo_slug}")
        if not graph_data:
            # Try loading from disk
            from pipeline.cache import load_graph
            graph_data = load_graph(repo_slug)
        if not graph_data:
            return "Code graph not found. Run review_pr first to build the graph."

        import networkx as nx
        try:
            graph = nx.node_link_graph(graph_data.get("_nx", graph_data))
        except Exception as exc:
            return f"Failed to load code graph: {exc}"

        # Get current PR changed files from session
        pr_ctx = session.get(f"pr_changed:{platform}:{workspace}:{repo_slug}")
        pr_changed_files: list[str] = pr_ctx if pr_ctx else []

        analyzer = ImpactAnalyzer()
        result = analyzer.get_entity_impact(graph, entity, pr_changed_files)

        if "error" in result:
            return result["error"]

        lines = [f"## Impact analysis: `{entity}`\n"]
        meta = result.get("metadata", {})
        lines.append(
            f"**Kind**: {meta.get('kind', '?')} | "
            f"**File**: `{meta.get('file_path', '?')}` | "
            f"**Lines**: {meta.get('line_start', '?')}-{meta.get('line_end', '?')}\n"
        )

        callers = result.get("callers", [])
        callees = result.get("callees", [])
        unupdated = result.get("unupdated_callers", [])

        lines.append(f"### Callers ({len(callers)})")
        for c in callers[:15]:
            flag = " ⚠️ NOT in PR" if not c.get("in_pr") else " ✅ in PR"
            lines.append(f"- `{c['id']}` — `{c['file']}`{flag}")

        lines.append(f"\n### Callees ({len(callees)})")
        for c in callees[:15]:
            lines.append(f"- `{c['id']}` — `{c['file']}`")

        if unupdated:
            lines.append(f"\n### ⚠️ Unupdated callers — potential breakage ({len(unupdated)})")
            for c in unupdated:
                lines.append(
                    f"- `{c['id']}` in `{c['file']}` was NOT updated. "
                    "Review if the change is backward-compatible."
                )

        return "\n".join(lines)
