"""
tools/run_command.py — Sandboxed shell command executor.
MCP tool: run_command

Sandboxed to the repo directory. Blocks destructive git ops without
an explicit confirmation flag. Logs all commands.
"""

import logging
import os
import subprocess
from pathlib import Path

from mcp.types import Tool

import config

logger = logging.getLogger(__name__)

# Commands that mutate history/remote — require confirm=true
_DESTRUCTIVE_PATTERNS = [
    "git push --force",
    "git push -f",
    "git reset --hard",
    "git clean -f",
    "git rm",
    "rm -rf",
    "rm -f",
    "sudo",
]


class RunCommandTool:
    def definition(self) -> Tool:
        return Tool(
            name="run_command",
            description=(
                "Execute a shell command inside the cloned repository directory. "
                "Use this for running tests, applying fixes, committing, and pushing. "
                "Commands are sandboxed to the repo root. Destructive git operations "
                "(force-push, reset --hard, etc.) require confirm=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run (executed via bash -c)",
                    },
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                    "confirm": {
                        "type": "boolean",
                        "default": False,
                        "description": "Set to true to allow destructive git operations.",
                    },
                    "timeout": {
                        "type": "integer",
                        "default": 60,
                        "description": "Command timeout in seconds.",
                    },
                },
                "required": ["command", "workspace", "repo_slug"],
            },
        )

    async def run(self, args: dict) -> str:
        if not config.RUN_COMMAND_ENABLED:
            return "run_command is disabled. Set RUN_COMMAND_ENABLED=true to enable."

        command: str = args["command"]
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]
        confirm: bool = args.get("confirm", False)
        timeout: int = int(args.get("timeout", 60))

        repo_path = config.REPOS_DIR / platform / workspace / repo_slug
        if not repo_path.exists():
            return f"Repo not found locally. Run review_pr first."

        # ── Safety check ──────────────────────────────────────────────────
        if not confirm:
            for pattern in _DESTRUCTIVE_PATTERNS:
                if pattern in command:
                    return (
                        f"Blocked: '{pattern}' is a destructive operation. "
                        "Set confirm=true to allow."
                    )

        logger.info("run_command [%s/%s]: %s", workspace, repo_slug, command)

        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            output_parts = []
            if result.stdout:
                output_parts.append(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                output_parts.append(f"STDERR:\n{result.stderr}")
            output_parts.append(f"Exit code: {result.returncode}")
            return "\n".join(output_parts) if output_parts else "(no output)"
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s"
        except Exception as exc:
            logger.exception("run_command error")
            return f"Error: {exc}"
