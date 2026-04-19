"""
tools/review_history.py — Fetch and summarize past PR comments.
MCP tool: get_review_history
"""

import json
import logging
from typing import Optional

from mcp.types import Tool
from anthropic import AsyncAnthropic

import config
from pipeline.cache import load_review_history, save_review_history

logger = logging.getLogger(__name__)


class ReviewHistoryTool:
    def definition(self) -> Tool:
        return Tool(
            name="get_review_history",
            description=(
                "Fetch past PR review comments for a specific file or directory. "
                "Automatically uses an LLM to summarize recurring patterns into "
                "a dynamic playbook."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path or directory to filter comments for (e.g. 'src/' or 'src/utils.py').",
                    },
                    "author": {
                        "type": "string",
                        "description": "Optional author name to filter by.",
                    },
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                },
                "required": ["workspace", "repo_slug"],
            },
        )

    async def run(self, args: dict) -> str:
        file_path: str = args.get("file_path", "")
        author: str = args.get("author", "")
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]

        comments = self._get_cached_history(platform, workspace, repo_slug)
        if comments is None:
            comments = self._fetch_history(platform, workspace, repo_slug)
            if comments:
                save_review_history(repo_slug, {"comments": comments})

        if not comments:
            return "No review history found or failed to fetch."

        # Filter
        filtered = []
        for c in comments:
            if file_path and file_path not in c.get("file_path", ""):
                continue
            if author and author.lower() not in c.get("author", "").lower():
                continue
            filtered.append(c)

        if not filtered:
            return f"No historical comments match file='{file_path}' author='{author}'"

        # Summarize with LLM
        summary = await self._summarize(filtered, file_path)

        lines = [
            f"### Review History & Dynamic Playbook (matches: {len(filtered)})",
            "**Playbook Rules (auto-generated from past reviews):**",
            summary,
            "\n**Recent raw comments:**",
        ]
        for c in filtered[:10]:
            lines.append(f"- **{c.get('author')}** on `{c.get('file_path')}` (PR #{c.get('pr_id')}): {c.get('content')}")

        return "\n".join(lines)

    def _get_cached_history(self, platform: str, workspace: str, slug: str) -> Optional[list]:
        data = load_review_history(slug)
        return data.get("comments") if data else None

    def _fetch_history(self, platform: str, workspace: str, slug: str) -> list[dict]:
        if platform == "bitbucket":
            from adapters.bitbucket import BitbucketAdapter
            return BitbucketAdapter().get_review_history(workspace, slug)
        elif platform == "github":
            from adapters.github import GitHubAdapter
            return GitHubAdapter().get_review_history(workspace, slug)
        return []

    async def _summarize(self, comments: list[dict], scope: str) -> str:
        if not config.ANTHROPIC_API_KEY:
            return "(LLM API key not set. Cannot generate playbook summary.)"

        prompt = (
            f"You are analyzing past code review comments for the scope: '{scope}'.\n"
            "Below are the raw comments. Extract 3 to 5 clear, actionable 'playbook rules' "
            "or recurring patterns that reviewers frequently point out. "
            "Format as a bulleted markdown list.\n\n"
        )
        for i, c in enumerate(comments[:30]):
            prompt += f"[{i+1}] {c.get('file_path')}: {c.get('content')}\n"

        client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        try:
            resp = await client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=500,
                system="Extract precise, actionable review patterns.",
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.content[0].text
        except Exception as exc:
            return f"Failed to generate summary: {exc}"
