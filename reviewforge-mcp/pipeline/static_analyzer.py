"""
pipeline/static_analyzer.py — Subprocess wrappers for static analysis tools.

Supported: ruff, mypy, bandit, hlint, semgrep, eslint.
Gracefully degrades: missing tools are logged, review proceeds without them.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Map tool name → availability (checked at import)
_AVAILABLE: dict[str, bool] = {}


def _check_tools() -> None:
    for tool in ["ruff", "mypy", "bandit", "hlint", "semgrep", "eslint"]:
        _AVAILABLE[tool] = shutil.which(tool) is not None
    unavailable = [t for t, ok in _AVAILABLE.items() if not ok]
    if unavailable:
        logger.info("Static analysis tools not installed (review works without them): %s", unavailable)


_check_tools()


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> tuple[str, str, int]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "Timed out", 1
    except FileNotFoundError:
        return "", f"Tool not found: {cmd[0]}", 1


class StaticAnalyzer:

    def run_all(self, repo_path: Path, changed_files: list[str]) -> dict[str, list[dict]]:
        """Run all available tools on changed files. Returns structured findings."""
        findings: dict[str, list[dict]] = {}

        py_files = [f for f in changed_files if f.endswith(".py")]
        hs_files = [f for f in changed_files if f.endswith((".hs", ".lhs"))]
        js_files = [f for f in changed_files if f.endswith((".js", ".jsx", ".ts", ".tsx"))]

        if py_files:
            if _AVAILABLE.get("ruff"):
                findings["ruff"] = self.run_ruff(repo_path, py_files)
            if _AVAILABLE.get("mypy"):
                findings["mypy"] = self.run_mypy(repo_path, py_files)
            if _AVAILABLE.get("bandit"):
                findings["bandit"] = self.run_bandit(repo_path, py_files)

        if hs_files and _AVAILABLE.get("hlint"):
            findings["hlint"] = self.run_hlint(repo_path, hs_files)

        if _AVAILABLE.get("semgrep"):
            findings["semgrep"] = self.run_semgrep(repo_path, changed_files)

        return findings

    def run_one(self, tool: str, repo_path: Path, target: str) -> list[dict]:
        """Run a specific tool on a file or the full repo."""
        if not _AVAILABLE.get(tool):
            return [{"severity": "info", "message": f"{tool} is not installed."}]
        files = [target] if target else []
        dispatch = {
            "ruff": lambda: self.run_ruff(repo_path, files or self._py_files(repo_path)),
            "mypy": lambda: self.run_mypy(repo_path, files or self._py_files(repo_path)),
            "bandit": lambda: self.run_bandit(repo_path, files or self._py_files(repo_path)),
            "hlint": lambda: self.run_hlint(repo_path, files or self._hs_files(repo_path)),
            "semgrep": lambda: self.run_semgrep(repo_path, files),
        }
        fn = dispatch.get(tool)
        return fn() if fn else [{"severity": "error", "message": f"Unknown tool: {tool}"}]

    # ── Tool implementations ───────────────────────────────────────────────

    def run_ruff(self, repo_path: Path, files: list[str]) -> list[dict]:
        if not files:
            return []
        stdout, stderr, _ = _run(
            ["ruff", "check", "--output-format=json"] + files,
            cwd=repo_path,
        )
        results = []
        try:
            for item in json.loads(stdout):
                results.append({
                    "tool": "ruff",
                    "file": item.get("filename"),
                    "line": item.get("location", {}).get("row"),
                    "code": item.get("code"),
                    "message": item.get("message"),
                    "severity": "warning",
                    "url": item.get("url"),
                })
        except json.JSONDecodeError:
            if stdout.strip():
                results.append({"tool": "ruff", "raw": stdout})
        return results

    def run_mypy(self, repo_path: Path, files: list[str]) -> list[dict]:
        if not files:
            return []
        stdout, _, _ = _run(["mypy", "--no-error-summary"] + files, cwd=repo_path)
        results = []
        for line in stdout.splitlines():
            # Format: file.py:line: error: message  [error-code]
            parts = line.split(":", 3)
            if len(parts) >= 4:
                severity = "error" if "error:" in parts[2] else "warning"
                results.append({
                    "tool": "mypy",
                    "file": parts[0].strip(),
                    "line": parts[1].strip(),
                    "severity": severity,
                    "message": parts[3].strip(),
                })
        return results

    def run_bandit(self, repo_path: Path, files: list[str]) -> list[dict]:
        if not files:
            return []
        stdout, _, _ = _run(
            ["bandit", "-f", "json", "-q"] + files,
            cwd=repo_path,
        )
        results = []
        try:
            data = json.loads(stdout)
            for issue in data.get("results", []):
                results.append({
                    "tool": "bandit",
                    "file": issue.get("filename"),
                    "line": issue.get("line_number"),
                    "severity": issue.get("issue_severity", "").lower(),
                    "confidence": issue.get("issue_confidence"),
                    "message": issue.get("issue_text"),
                    "test_id": issue.get("test_id"),
                })
        except json.JSONDecodeError:
            pass
        return results

    def run_hlint(self, repo_path: Path, files: list[str]) -> list[dict]:
        if not files:
            return []
        stdout, _, _ = _run(["hlint", "--json"] + files, cwd=repo_path)
        results = []
        try:
            for item in json.loads(stdout):
                results.append({
                    "tool": "hlint",
                    "file": item.get("file"),
                    "line": item.get("startLine"),
                    "severity": item.get("severity", "warning").lower(),
                    "message": item.get("hint"),
                    "from": item.get("from"),
                    "to": item.get("to"),
                })
        except json.JSONDecodeError:
            pass
        return results

    def run_semgrep(self, repo_path: Path, files: list[str]) -> list[dict]:
        cmd = ["semgrep", "--json", "--config=auto"]
        if files:
            cmd += files
        else:
            cmd.append(str(repo_path))
        stdout, _, _ = _run(cmd, cwd=repo_path, timeout=120)
        results = []
        try:
            data = json.loads(stdout)
            for item in data.get("results", []):
                results.append({
                    "tool": "semgrep",
                    "file": item.get("path"),
                    "line": item.get("start", {}).get("line"),
                    "severity": item.get("extra", {}).get("severity", "warning").lower(),
                    "message": item.get("extra", {}).get("message"),
                    "rule_id": item.get("check_id"),
                })
        except json.JSONDecodeError:
            pass
        return results

    # ── Helpers ────────────────────────────────────────────────────────────

    def _py_files(self, repo_path: Path) -> list[str]:
        return [str(p.relative_to(repo_path)) for p in repo_path.rglob("*.py")][:100]

    def _hs_files(self, repo_path: Path) -> list[str]:
        return [str(p.relative_to(repo_path)) for p in repo_path.rglob("*.hs")][:100]
