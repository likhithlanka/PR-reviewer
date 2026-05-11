"""
ReviewForge MCP — Configuration loader.
Reads all settings from environment variables (or .env file via python-dotenv).
Values are read lazily from os.environ so MCP server env injections work
even if they arrive after module import.
"""

import os
from pathlib import Path

# Try loading .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass


def _env(key: str, default: str = "") -> str:
    """Read an env var lazily — always checks os.environ first."""
    return os.environ.get(key, default)


# ── Bitbucket ──────────────────────────────────────────────────────────────
BITBUCKET_TOKEN: str = ""  # placeholder; use _env() for live reads
BITBUCKET_WORKSPACE: str = ""

# Set BITBUCKET_URL for self-hosted Data Center (e.g. https://bitbucket.yourcompany.net).
# Leave empty for Bitbucket Cloud (api.bitbucket.org).
BITBUCKET_URL: str = ""


class _BitbucketConfig:
    """Lazy accessor that always reads from os.environ."""

    @property
    def BITBUCKET_TOKEN(self) -> str:
        return os.environ.get("BITBUCKET_TOKEN", "")

    @property
    def BITBUCKET_URL(self) -> str:
        return os.environ.get("BITBUCKET_URL", "")

    @property
    def BITBUCKET_WORKSPACE(self) -> str:
        return os.environ.get("BITBUCKET_WORKSPACE", "")


bitbucket = _BitbucketConfig()

# ── GitHub ─────────────────────────────────────────────────────────────────
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# ── LLM ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-opus-4-5")

# ── Embeddings ─────────────────────────────────────────────────────────────
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
# Set to "openai" to use OpenAI text-embedding-3-small instead of local model
EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "local")

# ── Confluence / Notion ────────────────────────────────────────────────────
CONFLUENCE_URL: str = os.getenv("CONFLUENCE_URL", "")
CONFLUENCE_USERNAME: str = os.getenv("CONFLUENCE_USERNAME", "")
CONFLUENCE_API_TOKEN: str = os.getenv("CONFLUENCE_API_TOKEN", "")
NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
GOOGLE_DOCS_CREDENTIALS_FILE: str = os.getenv("GOOGLE_DOCS_CREDENTIALS_FILE", "")

# ── Cache ──────────────────────────────────────────────────────────────────
CACHE_DIR: Path = Path(
    os.getenv("REVIEWFORGE_CACHE_DIR", str(Path.home() / ".reviewforge"))
)
REPOS_DIR: Path = CACHE_DIR / "repos"
GRAPHS_DIR: Path = CACHE_DIR / "graphs"
VECTORS_DIR: Path = CACHE_DIR / "vectors"
HISTORY_DIR: Path = CACHE_DIR / "history"

# Ensure cache directories exist
for _d in [REPOS_DIR, GRAPHS_DIR, VECTORS_DIR, HISTORY_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Behaviour ──────────────────────────────────────────────────────────────
REPO_TTL_DAYS: int = int(os.getenv("REPO_TTL_DAYS", "30"))
HISTORY_REFRESH_HOURS: int = int(os.getenv("HISTORY_REFRESH_HOURS", "24"))
COCHANGE_LOOKBACK_MONTHS: int = int(os.getenv("COCHANGE_LOOKBACK_MONTHS", "6"))
COCHANGE_THRESHOLD: float = float(os.getenv("COCHANGE_THRESHOLD", "0.5"))
MAX_CONTEXT_TOKENS: int = int(os.getenv("MAX_CONTEXT_TOKENS", "12000"))
RUN_COMMAND_ENABLED: bool = os.getenv("RUN_COMMAND_ENABLED", "true").lower() == "true"

# ── Agent-driven review payload ───────────────────────────────────────────
CONTEXT_DOC_PREVIEW_CHARS: int = int(os.getenv("CONTEXT_DOC_PREVIEW_CHARS", "3000"))
PAYLOAD_MAX_IMPACT_ITEMS: int = int(os.getenv("PAYLOAD_MAX_IMPACT_ITEMS", "20"))
PAYLOAD_MAX_COCHANGE_ITEMS: int = int(os.getenv("PAYLOAD_MAX_COCHANGE_ITEMS", "10"))
PAYLOAD_MAX_STATIC_TOP: int = int(os.getenv("PAYLOAD_MAX_STATIC_TOP", "5"))

# ── Large-diff handling ───────────────────────────────────────────────────
DIFF_FETCH_TIMEOUT: int = int(os.getenv("DIFF_FETCH_TIMEOUT", "120"))
