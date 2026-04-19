"""
adapters/github.py — GitHub REST API interactions.
"""

import logging
from typing import Any

from github import Github
from github import Auth

import config

logger = logging.getLogger(__name__)


class GitHubAdapter:
    def __init__(self) -> None:
        token = config.GITHUB_TOKEN
        if not token:
            logger.warning("GitHub token not set in config.")
            self.client = None
        else:
            auth = Auth.Token(token)
            self.client = Github(auth=auth)

    def get_pr(self, workspace: str, repo_slug: str, pr_id: int) -> dict[str, Any]:
        if not self.client:
            return {}
        try:
            repo = self.client.get_repo(f"{workspace}/{repo_slug}")
            pr = repo.get_pull(pr_id)
            return {
                "id": pr.number,
                "title": pr.title,
                "description": pr.body,
                "state": pr.state,
                "source_branch": pr.head.ref,
                "target_branch": pr.base.ref,
                "author": pr.user.login,
            }
        except Exception as exc:
            logger.error("Failed to fetch GitHub PR %s/%s#%s: %s", workspace, repo_slug, pr_id, exc)
            return {}

    def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        if not self.client:
            return ""
        try:
            import requests
            url = f"https://api.github.com/repos/{workspace}/{repo_slug}/pulls/{pr_id}"
            headers = {
                "Authorization": f"token {config.GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3.diff",
            }
            r = requests.get(url, headers=headers)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            logger.error("Failed to fetch GitHub diff: %s", exc)
            return ""

    def get_pr_comments(self, workspace: str, repo_slug: str, pr_id: int) -> list[dict]:
        if not self.client:
            return []
        try:
            repo = self.client.get_repo(f"{workspace}/{repo_slug}")
            pr = repo.get_pull(pr_id)
            comments = []
            for c in pr.get_review_comments():
                comments.append({
                    "id": c.id,
                    "content": c.body,
                    "author": c.user.login,
                    "file_path": c.path,
                    "line": c.line,
                    "created_on": c.created_at.isoformat(),
                })
            return comments
        except Exception as exc:
            logger.error("Failed to fetch GitHub comments: %s", exc)
            return []

    def get_review_history(self, workspace: str, repo_slug: str, limit: int = 50) -> list[dict]:
        if not self.client:
            return []
        try:
            repo = self.client.get_repo(f"{workspace}/{repo_slug}")
            prs = repo.get_pulls(state="closed", sort="updated", direction="desc")
            
            all_comments = []
            count = 0
            for pr in prs:
                if not pr.merged:
                    continue
                count += 1
                if count > limit:
                    break
                try:
                    for c in pr.get_review_comments():
                        if not c.path:
                            continue
                        all_comments.append({
                            "pr_id": pr.number,
                            "pr_title": pr.title,
                            "file_path": c.path,
                            "content": c.body,
                            "author": c.user.login,
                            "created_on": c.created_at.isoformat(),
                        })
                except Exception:
                    pass
            return all_comments
        except Exception as exc:
            logger.error("Failed to fetch review history: %s", exc)
            return []
