"""
tools/pr_comments.py — Fetch current PR comments.
MCP tool: get_pr_comments
"""

import logging
from mcp.types import Tool

logger = logging.getLogger(__name__)


class PRCommentsTool:
    def definition(self) -> Tool:
        return Tool(
            name="get_pr_comments",
            description="Fetch the active comment threads on a specified PR.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pr_id": {"type": "integer"},
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                },
                "required": ["pr_id", "workspace", "repo_slug"],
            },
        )

    async def run(self, args: dict) -> str:
        pr_id: int = int(args["pr_id"])
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]

        comments = []
        if platform == "bitbucket":
            from adapters.bitbucket import BitbucketAdapter
            comments = BitbucketAdapter().get_pr_comments(workspace, repo_slug, pr_id)
        elif platform == "github":
            from adapters.github import GitHubAdapter
            comments = GitHubAdapter().get_pr_comments(workspace, repo_slug, pr_id)
        else:
            return f"Unsupported platform: {platform}"

        if not comments:
            return "No comments found on this PR."

        lines = [f"### Comments for PR #{pr_id}"]
        for c in comments:
            lines.append(
                f"- **{c.get('author')}** on `{c.get('file_path')}:{c.get('line')}` "
                f"({c.get('created_on')}):\n  {c.get('content')}"
            )
        return "\n".join(lines)
