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

        # Snap the requested line to the nearest real added line in the diff.
        # This prevents misplaced anchors when the LLM produces a line number that
        # is a context/deleted line rather than an actually-added (+) line.
        if file_path and line is not None:
            line = self._snap_line(file_path, line, platform, workspace, repo_slug, pr_id)

        if platform == "bitbucket":
            from adapters.bitbucket import BitbucketAdapter
            result, error = BitbucketAdapter().post_pr_comment(workspace, repo_slug, pr_id, text, file_path, line)
        else:
            return f"Unsupported platform: {platform}"

        if error:
            return f"Failed to post comment on PR #{pr_id}: {error}"

        return f"Comment posted on PR #{pr_id} (comment ID: {result.get('id')})"

    def _snap_line(
        self,
        file_path: str,
        requested_line: int,
        platform: str,
        workspace: str,
        repo_slug: str,
        pr_id: int,
    ) -> int:
        """
        Return the closest added-line number from the diff for this file.

        Falls back to requested_line if no diff data is cached (e.g. the pipeline
        wasn't run in this session), so the call degrades gracefully.
        """
        from pipeline.cache import session
        from pipeline.diff_parser import snap_to_added_line

        added_lines_map = session.get(f"added_lines:{platform}:{workspace}:{repo_slug}:{pr_id}")
        if not added_lines_map:
            return requested_line

        snapped = snap_to_added_line(file_path, requested_line, added_lines_map)
        if snapped is None:
            return requested_line

        if snapped != requested_line:
            logger.info(
                "Snapped inline comment line %d → %d for %s (nearest added line in diff)",
                requested_line, snapped, file_path,
            )
        return snapped
