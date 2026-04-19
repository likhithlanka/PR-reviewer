"""
tools/post_comment.py — Post a comment on a PR.
MCP tool: post_pr_comment
"""

import logging
from typing import Optional
from mcp.types import Tool

logger = logging.getLogger(__name__)


class PostCommentTool:
    def definition(self) -> Tool:
        return Tool(
            name="post_pr_comment",
            description="Post a comment on a pull request. Optionally anchor it to a specific file and line.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pr_id": {"type": "integer", "description": "PR ID"},
                    "workspace": {"type": "string", "description": "Workspace/project key (e.g. 'JBIZ')"},
                    "repo_slug": {"type": "string", "description": "Repository slug"},
                    "text": {"type": "string", "description": "Comment text (supports Bitbucket markdown)"},
                    "file_path": {"type": "string", "description": "Optional file path to anchor the comment to"},
                    "line": {"type": "integer", "description": "Optional line number to anchor the comment to"},
                    "platform": {"type": "string", "default": "bitbucket"},
                },
                "required": ["pr_id", "workspace", "repo_slug", "text"],
            },
        )

    async def run(self, args: dict) -> str:
        pr_id: int = int(args["pr_id"])
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]
        text: str = args["text"]
        file_path: Optional[str] = args.get("file_path")
        line: Optional[int] = args.get("line")
        platform: str = args.get("platform", "bitbucket")

        if platform == "bitbucket":
            from adapters.bitbucket import BitbucketAdapter
            result, error = BitbucketAdapter().post_pr_comment(workspace, repo_slug, pr_id, text, file_path, line)
        else:
            return f"Unsupported platform: {platform}"

        if error:
            return f"Failed to post comment on PR #{pr_id}: {error}"

        return f"Comment posted on PR #{pr_id} (comment ID: {result.get('id')})"
