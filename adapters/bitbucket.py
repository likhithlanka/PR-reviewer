"""
adapters/bitbucket.py — Bitbucket REST API interactions.
"""

import logging
from typing import Any, Optional

from atlassian.bitbucket import Cloud

import config

logger = logging.getLogger(__name__)


class BitbucketAdapter:
    def __init__(self) -> None:
        token = config.BITBUCKET_TOKEN
        if not token:
            logger.warning("Bitbucket token not set in config.")
            self.client = None
        else:
            self.client = Cloud(url="https://api.bitbucket.org", token=token)

    def get_pr(self, workspace: str, repo_slug: str, pr_id: int) -> dict[str, Any]:
        if not self.client:
            return {}
        try:
            return self.client.workspaces.get(workspace).repositories.get(repo_slug).pullrequests.get(pr_id)
        except Exception as exc:
            logger.error("Failed to fetch Bitbucket PR %s/%s#%s: %s", workspace, repo_slug, pr_id, exc)
            return {}

    def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        if not self.client:
            return ""
        try:
            pr = self.client.workspaces.get(workspace).repositories.get(repo_slug).pullrequests.get(pr_id)
            # The atlassian-python-api doesn't directly expose the raw diff easily in all versions,
            # so we fetch it via requests directly if needed, or use the object:
            import requests
            url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diff"
            headers = {"Authorization": f"Bearer {config.BITBUCKET_TOKEN}"}
            r = requests.get(url, headers=headers)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            logger.error("Failed to fetch Bitbucket diff: %s", exc)
            return ""

    def get_pr_comments(self, workspace: str, repo_slug: str, pr_id: int) -> list[dict]:
        if not self.client:
            return []
        try:
            pr = self.client.workspaces.get(workspace).repositories.get(repo_slug).pullrequests.get(pr_id)
            comments = []
            for c in pr.comments():
                if c.get("deleted"):
                    continue
                comments.append({
                    "id": c.get("id"),
                    "content": c.get("content", {}).get("raw", ""),
                    "author": c.get("user", {}).get("display_name", ""),
                    "file_path": c.get("inline", {}).get("path"),
                    "line": c.get("inline", {}).get("to"),
                    "created_on": c.get("created_on"),
                })
            return comments
        except Exception as exc:
            logger.error("Failed to fetch Bitbucket comments: %s", exc)
            return []

    def get_review_history(self, workspace: str, repo_slug: str, limit: int = 50) -> list[dict]:
        """Fetch comments from recently merged PRs to build history."""
        if not self.client:
            return []
        try:
            repo = self.client.workspaces.get(workspace).repositories.get(repo_slug)
            prs = []
            for pr in repo.pullrequests.each(state="MERGED"):
                prs.append(pr)
                if len(prs) >= limit:
                    break

            all_comments = []
            for pr in prs:
                try:
                    for c in pr.comments():
                        if c.get("deleted") or not c.get("inline", {}).get("path"):
                            continue
                        all_comments.append({
                            "pr_id": pr.get("id"),
                            "pr_title": pr.get("title"),
                            "file_path": c.get("inline", {}).get("path"),
                            "content": c.get("content", {}).get("raw", ""),
                            "author": c.get("user", {}).get("display_name", ""),
                            "created_on": c.get("created_on"),
                        })
                except Exception:
                    pass
            return all_comments
        except Exception as exc:
            logger.error("Failed to fetch review history: %s", exc)
            return []
