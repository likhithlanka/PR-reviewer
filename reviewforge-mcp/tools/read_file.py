"""
tools/read_file.py — Read file from cached repo with optional AST context.
MCP tool: read_file
"""

import json
import logging
from pathlib import Path
from typing import Optional

from mcp.types import Tool

from pipeline.cache import session

logger = logging.getLogger(__name__)


class ReadFileTool:
    def definition(self) -> Tool:
        return Tool(
            name="read_file",
            description=(
                "Read any file from the cloned repository. "
                "Optionally includes AST context (function signatures, call relationships), "
                "sibling file names, and recent git history for the file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path within the repo (e.g. src/Foo.hs)",
                    },
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                    "include_ast_context": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include function signatures and call relationships from the code graph.",
                    },
                    "include_git_history": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include last 5 commits that touched this file.",
                    },
                },
                "required": ["file_path", "workspace", "repo_slug"],
            },
        )

    async def run(self, args: dict) -> str:
        from pipeline.repo_manager import RepoManager

        file_path_rel: str = args["file_path"]
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]
        include_ast: bool = args.get("include_ast_context", False)
        include_history: bool = args.get("include_git_history", False)

        import config
        repo_path = config.REPOS_DIR / platform / workspace / repo_slug
        if not repo_path.exists():
            return f"Repo not found locally. Run review_pr first to clone {workspace}/{repo_slug}."

        full_path = repo_path / file_path_rel
        if not full_path.exists():
            return f"File not found: {file_path_rel}"

        result_parts: list[str] = []

        # ── File content ──────────────────────────────────────────────────
        try:
            content = full_path.read_text(errors="replace")
            result_parts.append(f"=== {file_path_rel} ===\n{content}")
        except Exception as exc:
            return f"Error reading file: {exc}"

        # ── AST context ───────────────────────────────────────────────────
        if include_ast:
            graph_data = session.get(f"graph:{platform}:{workspace}:{repo_slug}")
            if graph_data:
                file_nodes = [
                    n for n, d in graph_data["nodes"].items()
                    if d.get("file_path", "").endswith(file_path_rel)
                ]
                if file_nodes:
                    ast_lines = ["\n=== AST Context ==="]
                    for node_id in file_nodes[:20]:
                        d = graph_data["nodes"][node_id]
                        callers = graph_data["edges"].get(node_id, {}).get("called_by", [])
                        callees = graph_data["edges"].get(node_id, {}).get("calls", [])
                        ast_lines.append(
                            f"  [{d.get('kind','?')}] {node_id}\n"
                            f"    callers: {callers[:5]}\n"
                            f"    calls:   {callees[:5]}"
                        )
                    result_parts.append("\n".join(ast_lines))
            else:
                result_parts.append("\n(AST context not available — run review_pr first)")

        # ── Git history ───────────────────────────────────────────────────
        if include_history:
            try:
                import git
                repo = git.Repo(str(repo_path))
                commits = list(repo.iter_commits(paths=file_path_rel, max_count=5))
                history_lines = ["\n=== Recent git history ==="]
                for c in commits:
                    history_lines.append(
                        f"  {c.hexsha[:8]} {c.authored_datetime.date()} "
                        f"{c.author.name}: {c.message.strip()[:80]}"
                    )
                result_parts.append("\n".join(history_lines))
            except Exception as exc:
                logger.warning("Git history failed: %s", exc)

        return "\n\n".join(result_parts)
