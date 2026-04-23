"""
adapters/bitbucket.py — Bitbucket API interactions.
Supports both Bitbucket Cloud (api.bitbucket.org) and
Bitbucket Data Center (self-hosted).

The platform is auto-detected from BITBUCKET_URL:
  - If set → Data Center mode (self-hosted)
  - If unset → Cloud mode (api.bitbucket.org)

Public methods return (result, error) tuples where error is a
human-readable string on failure, or None on success.
"""

import logging
import os
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)

CLOUD_API = "https://api.bitbucket.org/2.0"


class BitbucketAdapter:
    def __init__(self) -> None:
        self._token: str = config.bitbucket.BITBUCKET_TOKEN
        self._base_url: str = (config.bitbucket.BITBUCKET_URL or "").rstrip("/")
        self._is_cloud: bool = not self._base_url

        logger.info(
            "BitbucketAdapter init: mode=%s, base_url=%r, token_set=%s, "
            "env_BITBUCKET_URL=%r",
            self.mode_label, self._base_url, bool(self._token),
            os.environ.get("BITBUCKET_URL"),
        )

        if not self._token:
            logger.warning("Bitbucket token not set in config.")

    @property
    def mode_label(self) -> str:
        return "Cloud" if self._is_cloud else "Data Center"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    # ── Low-level HTTP helpers ──────────────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> tuple[Optional[dict], Optional[str]]:
        """Authenticated GET returning JSON. Returns (data, error)."""
        if not self._token:
            return None, "BITBUCKET_TOKEN is not set. Add it to your MCP server env config."
        try:
            r = requests.get(url, headers=self._headers, params=params, timeout=30)
            r.raise_for_status()
            return r.json(), None
        except requests.exceptions.HTTPError as exc:
            status = r.status_code
            if status == 401:
                detail = (
                    f"HTTP 401 Unauthorized for {url}. "
                    f"The token was rejected — it may be expired, invalid, or meant for a different "
                    f"Bitbucket instance. Current mode: {self.mode_label}"
                    + (f" (BITBUCKET_URL={self._base_url})" if not self._is_cloud else " (Cloud: api.bitbucket.org)")
                )
            elif status == 403:
                detail = f"HTTP 403 Forbidden for {url}. The token lacks permission to access this resource."
            elif status == 404:
                detail = f"HTTP 404 Not Found for {url}. Check the workspace/project key, repo slug, and PR ID."
            else:
                detail = f"HTTP {status} for {url}: {exc}"
            logger.error(detail)
            return None, detail
        except requests.exceptions.ConnectionError as exc:
            detail = f"Connection error for {url}: {exc}. Check BITBUCKET_URL and network access."
            logger.error(detail)
            return None, detail
        except Exception as exc:
            detail = f"Request failed for {url}: {exc}"
            logger.error(detail)
            return None, detail

    def _get_text(self, url: str, accept: str = "text/plain", timeout: int = 30) -> tuple[Optional[str], Optional[str]]:
        """Authenticated GET returning raw text. Returns (text, error)."""
        if not self._token:
            return None, "BITBUCKET_TOKEN is not set. Add it to your MCP server env config."
        try:
            headers = {**self._headers, "Accept": accept}
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.text, None
        except requests.exceptions.HTTPError as exc:
            status = r.status_code
            if status == 401:
                detail = (
                    f"HTTP 401 Unauthorized for {url}. "
                    f"The token was rejected — it may be expired, invalid, or meant for a different "
                    f"Bitbucket instance. Current mode: {self.mode_label}"
                    + (f" (BITBUCKET_URL={self._base_url})" if not self._is_cloud else " (Cloud: api.bitbucket.org)")
                )
            elif status == 403:
                detail = f"HTTP 403 Forbidden for {url}. The token lacks permission to access this resource."
            elif status == 404:
                detail = f"HTTP 404 Not Found for {url}. Check the workspace/project key, repo slug, and PR ID."
            else:
                detail = f"HTTP {status} for {url}: {exc}"
            logger.error(detail)
            return None, detail
        except requests.exceptions.ConnectionError as exc:
            detail = f"Connection error for {url}: {exc}. Check BITBUCKET_URL and network access."
            logger.error(detail)
            return None, detail
        except Exception as exc:
            detail = f"Request failed for {url}: {exc}"
            logger.error(detail)
            return None, detail

    def _post(self, url: str, json_body: dict, params: Optional[dict] = None) -> tuple[Optional[dict], Optional[str]]:
        """Authenticated POST returning JSON. Returns (data, error)."""
        if not self._token:
            return None, "BITBUCKET_TOKEN is not set. Add it to your MCP server env config."
        try:
            r = requests.post(url, headers=self._headers, json=json_body, params=params, timeout=30)
            r.raise_for_status()
            return r.json(), None
        except requests.exceptions.HTTPError as exc:
            status = r.status_code
            if status == 401:
                detail = f"HTTP 401 Unauthorized for {url}. Token may be expired or invalid."
            elif status == 403:
                detail = f"HTTP 403 Forbidden for {url}. Token lacks permission to post comments."
            else:
                detail = f"HTTP {status} for {url}: {exc}"
            logger.error(detail)
            return None, detail
        except Exception as exc:
            detail = f"POST failed for {url}: {exc}"
            logger.error(detail)
            return None, detail

    def _paginate_cloud(self, url: str, params: Optional[dict] = None, limit: int = 100) -> tuple[list[dict], Optional[str]]:
        """Follow Cloud paginated responses. Returns (results, error)."""
        results: list[dict] = []
        while url and len(results) < limit:
            data, err = self._get(url, params=params)
            if err:
                return results, err
            results.extend(data.get("values", []))
            url = data.get("next")
            params = None
        return results[:limit], None

    def _paginate_dc(self, url: str, params: Optional[dict] = None, limit: int = 100) -> tuple[list[dict], Optional[str]]:
        """Follow Data Center paginated responses. Returns (results, error)."""
        results: list[dict] = []
        params = dict(params or {})
        while len(results) < limit:
            data, err = self._get(url, params=params)
            if err:
                return results, err
            results.extend(data.get("values", []))
            if data.get("isLastPage", True):
                break
            start = data.get("nextPageStart")
            if start is None:
                break
            params["start"] = start
        return results[:limit], None

    # ── PR Operations ───────────────────────────────────────────────────────

    def get_pr(self, workspace: str, repo_slug: str, pr_id: int) -> tuple[dict[str, Any], Optional[str]]:
        """Returns (pr_data, error). error is None on success."""
        if self._is_cloud:
            return self._get_pr_cloud(workspace, repo_slug, pr_id)
        return self._get_pr_dc(workspace, repo_slug, pr_id)

    def _get_pr_cloud(self, workspace: str, repo_slug: str, pr_id: int) -> tuple[dict[str, Any], Optional[str]]:
        url = f"{CLOUD_API}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"
        data, err = self._get(url)
        if err:
            return {}, err
        try:
            return {
                "id": data["id"],
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "state": data.get("state", ""),
                "author": data.get("author", {}).get("display_name", ""),
                "source_branch": data.get("source", {}).get("branch", {}).get("name", ""),
                "target_branch": data.get("destination", {}).get("branch", {}).get("name", ""),
                "created_on": data.get("created_on", ""),
                "updated_on": data.get("updated_on", ""),
                "comment_count": data.get("comment_count", 0),
                "task_count": data.get("task_count", 0),
                "links": data.get("links", {}),
            }, None
        except (KeyError, TypeError) as exc:
            err = f"Failed to parse Cloud PR response: {exc}. Response keys: {list(data.keys())}"
            logger.error(err)
            return {}, err

    def _get_pr_dc(self, project: str, repo_slug: str, pr_id: int) -> tuple[dict[str, Any], Optional[str]]:
        url = f"{self._base_url}/rest/api/1.0/projects/{project}/repos/{repo_slug}/pull-requests/{pr_id}"
        data, err = self._get(url)
        if err:
            return {}, err
        try:
            return {
                "id": data["id"],
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "state": data.get("state", ""),
                "author": data.get("author", {}).get("displayName", ""),
                "source_branch": data.get("fromRef", {}).get("displayId", ""),
                "target_branch": data.get("toRef", {}).get("displayId", ""),
                "created_on": data.get("createdDate", ""),
                "updated_on": data.get("updatedDate", ""),
                "comment_count": data.get("properties", {}).get("commentCount", 0),
                "task_count": 0,
                "links": data.get("links", {}),
            }, None
        except (KeyError, TypeError) as exc:
            err = f"Failed to parse Data Center PR response: {exc}. Response keys: {list(data.keys())}"
            logger.error(err)
            return {}, err

    def post_pr_comment(self, workspace: str, repo_slug: str, pr_id: int, text: str, file_path: Optional[str] = None, line: Optional[int] = None) -> tuple[dict[str, Any], Optional[str]]:
        """Post a comment on a PR. Optionally anchored to a specific file/line."""
        if self._is_cloud:
            url = f"{CLOUD_API}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
            body: dict[str, Any] = {"content": {"raw": text}}
            if file_path:
                inline = {"path": file_path}
                if line is not None:
                    inline["to"] = line
                body["inline"] = inline
        else:
            url = f"{self._base_url}/rest/api/1.0/projects/{workspace}/repos/{repo_slug}/pull-requests/{pr_id}/comments"
            body = {"text": text}
            if file_path:
                anchor: dict[str, Any] = {"path": file_path, "lineType": "ADDED", "fileType": "TO"}
                if line is not None:
                    anchor["line"] = line
                body["anchor"] = anchor

        data, err = self._post(url, json_body=body)
        if err:
            return {}, err
        return {"id": data.get("id"), "created": True}, None

    def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> tuple[str, Optional[str]]:
        """Returns (diff_text, error). error is None on success."""
        if self._is_cloud:
            url = f"{CLOUD_API}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diff"
        else:
            url = f"{self._base_url}/rest/api/1.0/projects/{workspace}/repos/{repo_slug}/pull-requests/{pr_id}/diff"
        result, err = self._get_text(url, accept="text/plain", timeout=config.DIFF_FETCH_TIMEOUT)
        return result or "", err

    def get_pr_comments(self, workspace: str, repo_slug: str, pr_id: int) -> tuple[list[dict], Optional[str]]:
        """Returns (comments, error). error is None on success."""
        if self._is_cloud:
            return self._get_pr_comments_cloud(workspace, repo_slug, pr_id)
        return self._get_pr_comments_dc(workspace, repo_slug, pr_id)

    def _get_pr_comments_cloud(self, workspace: str, repo_slug: str, pr_id: int) -> tuple[list[dict], Optional[str]]:
        url = f"{CLOUD_API}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
        raw, err = self._paginate_cloud(url, params={"pagelen": 100})
        if err:
            return [], err
        comments = []
        for c in raw:
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
        return comments, None

    def _get_pr_comments_dc(self, project: str, repo_slug: str, pr_id: int) -> tuple[list[dict], Optional[str]]:
        url = f"{self._base_url}/rest/api/1.0/projects/{project}/repos/{repo_slug}/pull-requests/{pr_id}/activities"
        raw, err = self._paginate_dc(url, params={"limit": 100})
        if err:
            return [], err
        comments = []
        for act in raw:
            if act.get("action") != "COMMENTED":
                continue
            c = act.get("comment")
            if not c:
                continue
            anchor = c.get("anchor", {}) or {}
            comments.append({
                "id": c.get("id"),
                "content": c.get("text", ""),
                "author": c.get("author", {}).get("displayName", ""),
                "file_path": anchor.get("path"),
                "line": anchor.get("line") or anchor.get("dstLine"),
                "created_on": c.get("createdDate"),
            })
        return comments, None

    def get_review_history(self, workspace: str, repo_slug: str, limit: int = 50) -> tuple[list[dict], Optional[str]]:
        """Returns (comments, error). error is None on success."""
        if self._is_cloud:
            return self._get_review_history_cloud(workspace, repo_slug, limit)
        return self._get_review_history_dc(workspace, repo_slug, limit)

    def _get_review_history_cloud(self, workspace: str, repo_slug: str, limit: int) -> tuple[list[dict], Optional[str]]:
        url = f"{CLOUD_API}/repositories/{workspace}/{repo_slug}/pullrequests"
        prs, err = self._paginate_cloud(url, params={"state": "MERGED", "pagelen": 50}, limit=limit)
        if err:
            return [], err
        all_comments = []
        for pr in prs:
            pr_id = pr.get("id")
            pr_title = pr.get("title", "")
            comments_url = (
                pr.get("links", {}).get("comments", {}).get("href")
                or f"{CLOUD_API}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
            )
            try:
                raw, _ = self._paginate_cloud(comments_url, params={"pagelen": 100})
                for c in raw:
                    if c.get("deleted") or not c.get("inline", {}).get("path"):
                        continue
                    all_comments.append({
                        "pr_id": pr_id,
                        "pr_title": pr_title,
                        "file_path": c.get("inline", {}).get("path"),
                        "content": c.get("content", {}).get("raw", ""),
                        "author": c.get("user", {}).get("display_name", ""),
                        "created_on": c.get("created_on"),
                    })
            except Exception:
                pass
        return all_comments, None

    def _get_review_history_dc(self, project: str, repo_slug: str, limit: int) -> tuple[list[dict], Optional[str]]:
        url = f"{self._base_url}/rest/api/1.0/projects/{project}/repos/{repo_slug}/pull-requests"
        prs, err = self._paginate_dc(url, params={"state": "MERGED", "limit": 50}, limit=limit)
        if err:
            return [], err
        all_comments = []
        for pr in prs:
            pr_id = pr.get("id")
            pr_title = pr.get("title", "")
            activities_url = (
                f"{self._base_url}/rest/api/1.0/projects/{project}/repos/{repo_slug}"
                f"/pull-requests/{pr_id}/activities"
            )
            try:
                raw, _ = self._paginate_dc(activities_url, params={"limit": 100})
                for act in raw:
                    if act.get("action") != "COMMENTED":
                        continue
                    c = act.get("comment")
                    if not c:
                        continue
                    anchor = c.get("anchor", {}) or {}
                    path = anchor.get("path")
                    if not path:
                        continue
                    all_comments.append({
                        "pr_id": pr_id,
                        "pr_title": pr_title,
                        "file_path": path,
                        "content": c.get("text", ""),
                        "author": c.get("author", {}).get("displayName", ""),
                        "created_on": c.get("createdDate"),
                    })
            except Exception:
                pass
        return all_comments, None
