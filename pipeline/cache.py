"""
pipeline/cache.py — Session + persistent caching for ReviewForge.

Session cache: in-memory dict keyed by (platform, workspace, repo_slug, pr_id).
Persistent cache: JSON files in ~/.reviewforge/{history,graphs}/.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)


class SessionCache:
    """In-memory cache for the current MCP session."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def clear(self) -> None:
        self._store.clear()

    # Convenience: store/retrieve current PR context
    def set_pr_context(self, platform: str, workspace: str, repo: str, pr_id: str, ctx: dict) -> None:
        self.set(f"pr:{platform}:{workspace}:{repo}:{pr_id}", ctx)

    def get_pr_context(self, platform: str, workspace: str, repo: str, pr_id: str) -> Optional[dict]:
        return self.get(f"pr:{platform}:{workspace}:{repo}:{pr_id}")


# Module-level singleton
session = SessionCache()


# ── Persistent cache helpers ───────────────────────────────────────────────

def _history_path(repo_slug: str) -> Path:
    return config.HISTORY_DIR / f"{repo_slug}.json"


def load_review_history(repo_slug: str) -> Optional[dict]:
    """Load cached review history. Returns None if expired or missing."""
    path = _history_path(repo_slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        age_hours = (time.time() - data.get("cached_at", 0)) / 3600
        if age_hours > config.HISTORY_REFRESH_HOURS:
            logger.debug("Review history cache expired for %s", repo_slug)
            return None
        return data
    except Exception as exc:
        logger.warning("Failed to load review history cache: %s", exc)
        return None


def save_review_history(repo_slug: str, data: dict) -> None:
    """Persist review history with a timestamp."""
    path = _history_path(repo_slug)
    data["cached_at"] = time.time()
    path.write_text(json.dumps(data, indent=2))
    logger.debug("Saved review history cache → %s", path)


def _graph_path(repo_slug: str) -> Path:
    return config.GRAPHS_DIR / f"{repo_slug}.json"


def load_graph(repo_slug: str) -> Optional[dict]:
    """Load serialized code graph JSON. Returns None if missing."""
    path = _graph_path(repo_slug)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning("Failed to load graph cache: %s", exc)
        return None


def save_graph(repo_slug: str, graph_data: dict) -> None:
    """Persist code graph as JSON."""
    path = _graph_path(repo_slug)
    path.write_text(json.dumps(graph_data, indent=2))
    logger.debug("Saved code graph → %s", path)
