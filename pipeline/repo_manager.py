"""
pipeline/repo_manager.py — Clone/pull/cache repositories.

Repos are cached at ~/.reviewforge/repos/{platform}/{workspace}/{slug}/
First run: git clone. Subsequent runs: git fetch + checkout.
Repos older than REPO_TTL_DAYS without access are garbage-collected.
"""

import logging
import os
import shutil
import time
from urllib.parse import quote
from pathlib import Path
from typing import Optional

import git  # GitPython

import config

logger = logging.getLogger(__name__)


class RepoManager:
    """Manages local clones of remote repositories."""

    def ensure_repo(
        self,
        platform: str,
        workspace: str,
        repo_slug: str,
        branch: str,
        clone_url: Optional[str] = None,
    ) -> Path:
        """
        Ensure the repo is cloned and on the correct branch.
        Returns the local path to the repo root.
        """
        repo_path = self._local_path(platform, workspace, repo_slug)

        if repo_path.exists() and (repo_path / ".git").exists():
            logger.info("Repo found locally, pulling: %s", repo_path)
            self._pull(repo_path, branch)
        else:
            if not clone_url:
                clone_url = self._default_clone_url(platform, workspace, repo_slug)
            logger.info("Cloning %s → %s", clone_url, repo_path)
            self._clone(clone_url, repo_path, branch)

        # Update last-accessed timestamp
        self._touch(repo_path)
        return repo_path

    # ── Internal helpers ───────────────────────────────────────────────────

    def _local_path(self, platform: str, workspace: str, slug: str) -> Path:
        return config.REPOS_DIR / platform / workspace / slug

    def _default_clone_url(self, platform: str, workspace: str, slug: str) -> str:
        if platform == "bitbucket":
            token = config.bitbucket.BITBUCKET_TOKEN
            base = config.bitbucket.BITBUCKET_URL
            if base:
                # Data Center (self-hosted) — uses HTTP Basic Auth
                host = base.replace("https://", "").replace("http://", "")
                username = os.environ.get("BITBUCKET_USERNAME", "")
                if token:
                    # Use username:token@ format for Data Center
                    user = quote(username or token, safe="")
                    return f"https://{user}:{token}@{host}/scm/{workspace}/{slug}.git"
                return f"https://{host}/scm/{workspace}/{slug}.git"
            # Cloud — uses x-token-auth scheme
            if token:
                return f"https://x-token-auth:{token}@bitbucket.org/{workspace}/{slug}.git"
            return f"https://bitbucket.org/{workspace}/{slug}.git"
        elif platform == "github":
            token = config.GITHUB_TOKEN
            if token:
                return f"https://{token}@github.com/{workspace}/{slug}.git"
            return f"https://github.com/{workspace}/{slug}.git"
        else:
            raise ValueError(f"Unsupported platform: {platform}")

    def _clone(self, url: str, path: Path, branch: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        git.Repo.clone_from(url, str(path), branch=branch, depth=None)

    def _pull(self, path: Path, branch: str) -> None:
        repo = git.Repo(str(path))
        origin = repo.remotes.origin
        origin.fetch()
        repo.git.checkout(branch)
        origin.pull(branch)

    def _touch(self, repo_path: Path) -> None:
        ts_file = repo_path / ".reviewforge_accessed"
        ts_file.write_text(str(time.time()))

    # ── Garbage collection ─────────────────────────────────────────────────

    def gc_old_repos(self) -> list[str]:
        """Remove repos not accessed within REPO_TTL_DAYS. Returns removed slugs."""
        removed = []
        for ts_file in config.REPOS_DIR.rglob(".reviewforge_accessed"):
            try:
                accessed = float(ts_file.read_text())
                age_days = (time.time() - accessed) / 86400
                if age_days > config.REPO_TTL_DAYS:
                    repo_path = ts_file.parent
                    logger.info("GC: removing old repo %s (%.1f days)", repo_path, age_days)
                    shutil.rmtree(repo_path, ignore_errors=True)
                    removed.append(str(repo_path))
            except Exception as exc:
                logger.warning("GC error for %s: %s", ts_file, exc)
        return removed

    def get_repo(self, path: Path) -> git.Repo:
        """Return a GitPython Repo object for the given path."""
        return git.Repo(str(path))

    def get_changed_files(self, repo_path: Path, base_branch: str, pr_branch: str) -> list[str]:
        """Return list of files changed between base and PR branch."""
        repo = git.Repo(str(repo_path))
        try:
            diff = repo.git.diff("--name-only", f"origin/{base_branch}...{pr_branch}")
            return [f for f in diff.splitlines() if f]
        except Exception as exc:
            logger.warning("Could not get changed files: %s", exc)
            return []
