"""
pipeline/branch_analyzer.py — Deterministic branch completeness analysis.

For each function call that appears in a changed diff hunk, walks the Python AST
to determine:
  1. Is the call inside a conditional (if/elif/else)?
  2. What do ALL branches of that conditional do?
  3. Is there an else branch, and what does it call?

This produces VERIFIED FACTS like:
  "track_multiple_incoming is called in BOTH branches:
   - if-branch (line 545): has_multiple_incoming=True
   - else-branch (line 548): has_multiple_incoming=False"

These facts are injected into the review context so the reviewer never needs
to guess about branch structure. The reviewer should TRUST these facts and
not make independent structural claims that contradict them.

This solves the class of false positives where the reviewer sees one branch
in the diff context and concludes the other branch doesn't exist.
"""

import ast
import logging
from pathlib import Path
from typing import Any, Optional

from pipeline.diff_parser import parse_changed_ranges
from pipeline.utils import file_in_changeset

logger = logging.getLogger(__name__)


def analyze_branches(
    diff_text: str,
    changed_files: list[str],
    repo_path: Path,
) -> list[dict[str, Any]]:
    """
    For each changed file, parse the full AST and analyze every function call
    that falls within a changed diff hunk. For calls inside conditionals,
    report the complete branch structure.

    Returns a list of verified branch analysis results.
    """
    ranges = parse_changed_ranges(diff_text)
    if not ranges:
        return []

    results: list[dict[str, Any]] = []

    for diff_file, hunks in ranges.items():
        # Find the actual file on disk
        file_path = _resolve_file(diff_file, changed_files, repo_path)
        if not file_path or not file_path.exists():
            continue
        if not file_path.suffix == ".py":
            continue  # AST analysis only for Python

        try:
            source = file_path.read_text(errors="replace")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, Exception) as exc:
            logger.warning("branch_analyzer: cannot parse %s: %s", file_path, exc)
            continue

        source_lines = source.splitlines()

        # Find all function calls on changed lines
        for hunk_start, hunk_end in hunks:
            call_nodes = _find_calls_in_range(tree, hunk_start, hunk_end)
            for call_node in call_nodes:
                branch_info = _analyze_enclosing_conditional(
                    tree, call_node, source_lines, str(diff_file),
                )
                if branch_info:
                    results.append(branch_info)

    # Deduplicate by (file, conditional_line)
    seen: set[tuple[str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for r in results:
        key = (r["file"], r["conditional_line"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    logger.info("Branch analysis: %d verified conditional structures found", len(deduped))
    return deduped


def _resolve_file(
    diff_file: str, changed_files: list[str], repo_path: Path,
) -> Optional[Path]:
    """Resolve a diff file path to an actual path on disk."""
    # Try direct
    candidate = repo_path / diff_file
    if candidate.exists():
        return candidate
    # Try matching against changed_files
    for cf in changed_files:
        if file_in_changeset(diff_file, [cf]) or file_in_changeset(cf, [diff_file]):
            candidate = repo_path / cf
            if candidate.exists():
                return candidate
    return None


def _find_calls_in_range(
    tree: ast.Module, start: int, end: int,
) -> list[ast.Call]:
    """Find all ast.Call nodes whose line number falls within [start, end]."""
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and hasattr(node, "lineno"):
            if start <= node.lineno <= end:
                calls.append(node)
    return calls


def _get_call_name(node: ast.Call) -> str:
    """Extract a readable name from an ast.Call node."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # obj.method → try to get "obj.method"
        parts = []
        current = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return "<unknown>"


def _get_call_kwargs_summary(node: ast.Call) -> dict[str, str]:
    """Extract keyword argument values as strings."""
    kwargs = {}
    for kw in node.keywords:
        if kw.arg:
            try:
                kwargs[kw.arg] = ast.unparse(kw.value)
            except Exception:
                kwargs[kw.arg] = "?"
    return kwargs


def _analyze_enclosing_conditional(
    tree: ast.Module,
    call_node: ast.Call,
    source_lines: list[str],
    file_path: str,
) -> Optional[dict[str, Any]]:
    """
    Walk up the AST from a call node to find its enclosing If statement.
    If found, analyze ALL branches of that If (if/elif/else) and report
    what each branch does.
    """
    call_line = call_node.lineno
    call_name = _get_call_name(call_node)

    # Find the enclosing If node by walking the tree and building parent map
    parent_map: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    # Walk up from the call's enclosing statement to find an If
    enclosing_if = _find_enclosing_if(call_node, parent_map)
    if not enclosing_if:
        return None  # Not inside a conditional — nothing to verify

    # Analyze all branches of this If statement
    branches = _extract_branches(enclosing_if, call_name, source_lines)

    if not branches:
        return None

    # Build the condition text
    try:
        condition_text = ast.unparse(enclosing_if.test)
    except Exception:
        condition_text = _get_line_text(source_lines, enclosing_if.lineno)

    return {
        "file": file_path,
        "call_name": call_name,
        "call_line": call_line,
        "conditional_line": enclosing_if.lineno,
        "condition": condition_text,
        "has_else": bool(enclosing_if.orelse),
        "branches": branches,
        "verification": "VERIFIED_BY_AST",
    }


def _find_enclosing_if(
    node: ast.AST, parent_map: dict[int, ast.AST],
) -> Optional[ast.If]:
    """Walk up the parent chain to find the nearest enclosing If statement."""
    current = node
    # Walk up through expressions to find the enclosing statement
    for _ in range(50):  # Safety limit
        parent = parent_map.get(id(current))
        if parent is None:
            return None
        if isinstance(parent, ast.If):
            return parent
        # Stop walking up at function/class/module boundaries
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            return None
        current = parent
    return None


def _extract_branches(
    if_node: ast.If,
    target_call_name: str,
    source_lines: list[str],
) -> list[dict[str, Any]]:
    """
    Extract information about each branch (if/elif/else) of an If statement,
    specifically looking for calls to the target function in each branch.
    """
    branches = []

    # If-branch (body)
    if_calls = _find_calls_by_name(if_node.body, target_call_name)
    branch_info: dict[str, Any] = {
        "type": "if",
        "line_start": if_node.lineno,
        "condition": _safe_unparse(if_node.test),
    }
    if if_calls:
        branch_info["calls_target"] = True
        branch_info["call_details"] = [
            {
                "line": c.lineno,
                "kwargs": _get_call_kwargs_summary(c),
            }
            for c in if_calls
        ]
    else:
        branch_info["calls_target"] = False
        branch_info["summary"] = _summarize_branch(if_node.body, source_lines)
    branches.append(branch_info)

    # Elif/else branches
    orelse = if_node.orelse
    while orelse:
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            # elif
            elif_node = orelse[0]
            elif_calls = _find_calls_by_name(elif_node.body, target_call_name)
            branch_info = {
                "type": "elif",
                "line_start": elif_node.lineno,
                "condition": _safe_unparse(elif_node.test),
            }
            if elif_calls:
                branch_info["calls_target"] = True
                branch_info["call_details"] = [
                    {
                        "line": c.lineno,
                        "kwargs": _get_call_kwargs_summary(c),
                    }
                    for c in elif_calls
                ]
            else:
                branch_info["calls_target"] = False
                branch_info["summary"] = _summarize_branch(elif_node.body, source_lines)
            branches.append(branch_info)
            orelse = elif_node.orelse
        else:
            # else
            else_calls = _find_calls_by_name(orelse, target_call_name)
            # Find the line number of the else block
            else_line = orelse[0].lineno if orelse else 0
            branch_info = {
                "type": "else",
                "line_start": else_line,
            }
            if else_calls:
                branch_info["calls_target"] = True
                branch_info["call_details"] = [
                    {
                        "line": c.lineno,
                        "kwargs": _get_call_kwargs_summary(c),
                    }
                    for c in else_calls
                ]
            else:
                branch_info["calls_target"] = False
                branch_info["summary"] = _summarize_branch(orelse, source_lines)
            branches.append(branch_info)
            break

    return branches


def _find_calls_by_name(
    body: list[ast.stmt], target_name: str,
) -> list[ast.Call]:
    """Find all calls to target_name within a list of AST statements."""
    results = []
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                name = _get_call_name(node)
                if name == target_name:
                    results.append(node)
    return results


def _summarize_branch(
    body: list[ast.stmt], source_lines: list[str],
) -> str:
    """Create a short summary of what a branch does."""
    if not body:
        return "(empty)"
    summaries = []
    for stmt in body[:3]:  # First 3 statements
        try:
            text = ast.unparse(stmt)
            # Truncate long statements
            if len(text) > 100:
                text = text[:97] + "..."
            summaries.append(text)
        except Exception:
            text = _get_line_text(source_lines, stmt.lineno)
            summaries.append(text[:100])
    if len(body) > 3:
        summaries.append(f"... (+{len(body) - 3} more statements)")
    return "; ".join(summaries)


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<complex expression>"


def _get_line_text(source_lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(source_lines):
        return source_lines[lineno - 1].strip()
    return ""


def format_branch_analysis_section(results: list[dict[str, Any]]) -> str:
    """
    Format branch analysis results into a context section.
    These are VERIFIED FACTS that the reviewer must not contradict.
    """
    if not results:
        return ""

    parts = [
        "=== VERIFIED BRANCH ANALYSIS (Deterministic — AST-proven) ===",
        "The following branch structures were verified by parsing the actual source code.",
        "These are FACTS, not observations. Do NOT contradict them in your review.",
        "If the analysis says a function is called in both branches, it IS called in both branches.\n",
    ]

    for r in results:
        call_name = r["call_name"]
        file_path = r["file"]
        condition = r["condition"]

        parts.append(f"▸ `{call_name}` at {file_path}:{r['call_line']}")
        parts.append(f"  Condition (line {r['conditional_line']}): `{condition}`")
        parts.append(f"  Has else branch: {'YES' if r['has_else'] else 'NO'}")

        for branch in r["branches"]:
            branch_type = branch["type"].upper()
            line = branch.get("line_start", "?")
            if branch.get("calls_target"):
                details = branch.get("call_details", [{}])
                for detail in details:
                    kwargs_str = ", ".join(
                        f"{k}={v}" for k, v in detail.get("kwargs", {}).items()
                    )
                    call_line = detail.get("line", "?")
                    parts.append(
                        f"  [{branch_type}] (line {line}): Calls `{call_name}` at line {call_line}"
                        + (f" with {kwargs_str}" if kwargs_str else "")
                    )
            else:
                summary = branch.get("summary", "(no target call)")
                if branch["type"] == "if":
                    cond = branch.get("condition", "")
                    parts.append(f"  [{branch_type}] (line {line}, condition: `{cond}`): {summary}")
                elif branch["type"] == "elif":
                    cond = branch.get("condition", "")
                    parts.append(f"  [{branch_type}] (line {line}, condition: `{cond}`): {summary}")
                else:
                    parts.append(f"  [{branch_type}] (line {line}): {summary}")

        parts.append("")

    return "\n".join(parts)
