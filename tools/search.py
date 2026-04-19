"""
tools/search.py — Semantic + grep search MCP tool.
MCP tool: search
"""

import json
import logging
from pathlib import Path

from mcp.types import Tool

import config

logger = logging.getLogger(__name__)


class SearchTool:
    def definition(self) -> Tool:
        return Tool(
            name="search",
            description=(
                "Semantic search over the vectorized codebase AND vectorized documents "
                "(specs, Confluence pages, PDFs) in a single query. "
                "'What does the Visa spec say about DE 22?' hits both the embedded spec "
                "and the codebase implementation. Falls back to ripgrep for exact matches."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language or keyword search query.",
                    },
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                    "collection": {
                        "type": "string",
                        "enum": ["code", "docs", "both"],
                        "default": "both",
                        "description": "Which collection(s) to search.",
                    },
                    "n_results": {
                        "type": "integer",
                        "default": 10,
                        "description": "Number of results to return.",
                    },
                    "grep_fallback": {
                        "type": "boolean",
                        "default": True,
                        "description": "Also run ripgrep exact-match search and merge results.",
                    },
                },
                "required": ["query", "workspace", "repo_slug"],
            },
        )

    async def run(self, args: dict) -> str:
        from pipeline.vectorizer import Vectorizer

        query: str = args["query"]
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]
        collection: str = args.get("collection", "both")
        n_results: int = int(args.get("n_results", 10))
        grep_fallback: bool = args.get("grep_fallback", True)

        vector_dir = config.VECTORS_DIR / repo_slug
        if not vector_dir.exists():
            return "Vector index not found. Run review_pr first to build the index."

        vectorizer = Vectorizer(repo_slug)
        results = vectorizer.search(query, n_results=n_results, collection=collection)

        # Optional grep fallback
        if grep_fallback:
            repo_path = config.REPOS_DIR / platform / workspace / repo_slug
            if repo_path.exists():
                grep_results = vectorizer.grep_search(query, repo_path)
                # Deduplicate with semantic results
                existing_files = {r.get("metadata", {}).get("file_path") for r in results}
                for gr in grep_results:
                    if gr.get("file") not in existing_files:
                        results.append(gr)

        if not results:
            return f"No results found for: {query}"

        # Format output
        lines = [f"### Search results for: `{query}`\n"]
        for i, r in enumerate(results[:n_results], 1):
            if r.get("collection") == "grep":
                lines.append(
                    f"**[{i}] grep match** — `{r.get('file')}` line {r.get('line')}\n"
                    f"```\n{r.get('content', '')}\n```\n"
                )
            else:
                meta = r.get("metadata", {})
                fp = meta.get("file_path", "")
                name = meta.get("name", "")
                kind = meta.get("kind", "")
                score = r.get("score", "")
                col = r.get("collection", "")
                lines.append(
                    f"**[{i}] [{col}] {kind} `{name}`** — `{fp}` "
                    f"(score: {score})\n"
                    f"```\n{r.get('content', '')[:500]}\n```\n"
                )

        return "\n".join(lines)
