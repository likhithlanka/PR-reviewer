"""
pipeline/source_extractor.py — Extract full source of changed functions.

Given the AST graph and a list of changed function node IDs, reads the actual
source files and returns the complete function bodies. This gives the reviewer
the full code — not just the diff hunks — so claims like "this branch doesn't
exist" can be verified against reality.

Also extracts "declaration context": constants, gauge definitions, config values,
and class-level attributes referenced near a changed function, so the reviewer
sees the contracts a function operates under.
"""

import logging
import re
from pathlib import Path
from typing import Any, Optional

import networkx as nx

logger = logging.getLogger(__name__)

# Patterns for declarations that define contracts (gauges, constants, configs)
_DECLARATION_PATTERNS = [
    # Python: variable assignments at module level (NAME = ..., NAME: type = ...)
    re.compile(r"^([A-Z_][A-Z0-9_]*)\s*(?::\s*\w+)?\s*=\s*(.+)", re.M),
    # Haskell: type signatures
    re.compile(r"^(\w+)\s*::\s*(.+)", re.M),
]


def extract_changed_function_sources(
    graph: nx.DiGraph,
    changed_function_nodes: list[str],
    repo_path: Path,
    max_lines_per_function: int = 150,
    changed_ranges: Optional[dict[str, list[tuple[int, int]]]] = None,
) -> list[dict[str, Any]]:
    """
    Read the full source of each changed function from disk.

    If changed_ranges is provided, each source line is annotated with whether
    it was actually modified in the diff. This prevents the reviewer from
    flagging pre-existing issues on unchanged lines.

    Returns a list of dicts:
        {
            "node_id": "path/to/file.py::function_name",
            "name": "function_name",
            "file_path": "path/to/file.py",
            "line_start": 42,
            "line_end": 78,
            "source": "def function_name(...):\n    ...",
            "annotated_source": "  42 |   def function_name(...):\n» 43 | +     new_line = ...",
            "declaration_context": ["GAUGE_NAME = Gauge(...)", ...],
        }
    """
    results = []

    for node_id in changed_function_nodes:
        if not graph.has_node(node_id):
            continue
        data = graph.nodes[node_id]
        if data.get("kind") not in ("function", "class"):
            continue

        file_path = data.get("file_path", "")
        line_start = data.get("line_start")
        line_end = data.get("line_end")
        name = data.get("name", "")

        if not file_path or line_start is None:
            continue

        fpath = Path(file_path)
        if not fpath.exists():
            continue

        try:
            all_lines = fpath.read_text(errors="replace").splitlines()
        except Exception:
            continue

        # Clamp line_end
        if line_end is None:
            line_end = min(line_start + max_lines_per_function, len(all_lines))
        line_end = min(line_end, line_start + max_lines_per_function)

        # Extract the function source (1-based to 0-based)
        source_lines = all_lines[max(0, line_start - 1): line_end]
        source = "\n".join(source_lines)

        # Annotate with changed-line markers
        annotated = _annotate_source(
            source_lines, line_start, file_path, changed_ranges,
        )

        # Extract declaration context: scan the same file for constants,
        # gauge definitions, or config values that the function references
        decl_context = _extract_declarations(all_lines, source, file_path)

        results.append({
            "node_id": node_id,
            "name": name,
            "file_path": file_path,
            "line_start": line_start,
            "line_end": line_end,
            "source": source,
            "annotated_source": annotated,
            "declaration_context": decl_context,
        })

    logger.info("Extracted source for %d changed functions", len(results))
    return results


def _annotate_source(
    source_lines: list[str],
    line_start: int,
    file_path: str,
    changed_ranges: Optional[dict[str, list[tuple[int, int]]]] = None,
) -> str:
    """
    Annotate each source line with a marker showing whether it was changed
    in the diff. Changed lines get '»', unchanged lines get ' '.

    Example output:
        42 |   def process(self):
      » 43 |       new_logic = True      ← CHANGED IN THIS PR
        44 |       existing_code()
    """
    if not changed_ranges:
        # No range info — just number the lines
        annotated = []
        for i, line in enumerate(source_lines):
            lineno = line_start + i
            annotated.append(f"  {lineno:4d} | {line}")
        return "\n".join(annotated)

    # Find which ranges apply to this file
    file_hunks: list[tuple[int, int]] = []
    for diff_file, hunks in changed_ranges.items():
        if file_path.endswith(diff_file) or diff_file in file_path:
            file_hunks.extend(hunks)

    annotated = []
    for i, line in enumerate(source_lines):
        lineno = line_start + i
        is_changed = any(hs <= lineno <= he for hs, he in file_hunks)
        marker = "»" if is_changed else " "
        annotated.append(f"{marker} {lineno:4d} | {line}")
    return "\n".join(annotated)


def _extract_declarations(
    all_lines: list[str],
    function_source: str,
    file_path: str,
) -> list[str]:
    """
    Find module-level declarations (constants, gauges, configs) in the same file
    that are referenced by the function source.

    Returns a list of declaration strings like:
        "Line 15: GAUGE_NAME = Gauge('metric_name', 'description', ['label1'])"
    """
    declarations = []
    full_text = "\n".join(all_lines)

    # Collect all module-level declarations
    module_decls: list[tuple[int, str, str]] = []  # (line_no, name, full_line)
    for i, line in enumerate(all_lines):
        stripped = line.strip()
        # Skip lines inside functions/classes (indented)
        if line and line[0] in (" ", "\t"):
            continue
        for pattern in _DECLARATION_PATTERNS:
            m = pattern.match(stripped)
            if m:
                module_decls.append((i + 1, m.group(1), stripped))
                break

    # Filter to declarations referenced in the function source
    for line_no, name, full_line in module_decls:
        if name in function_source:
            # Include multi-line declarations (e.g. Gauge(...) spanning lines)
            decl_lines = [full_line]
            # Check if declaration continues (open paren not closed)
            if full_line.count("(") > full_line.count(")"):
                for j in range(line_no, min(line_no + 10, len(all_lines))):
                    decl_lines.append(all_lines[j].strip())
                    combined = "\n".join(decl_lines)
                    if combined.count("(") <= combined.count(")"):
                        break
            declarations.append(f"Line {line_no}: {' '.join(decl_lines)}")

    return declarations


def format_function_sources_section(sources: list[dict]) -> str:
    """
    Format extracted sources into a context section for the reviewer.
    """
    if not sources:
        return ""

    parts = [
        "=== FULL SOURCE OF CHANGED FUNCTIONS ===",
        "(Complete function bodies for verification. Lines marked with » were",
        "changed in this PR. Lines WITHOUT » are unchanged context — do NOT",
        "report issues on unchanged lines. Only review lines marked with ».)\n",
    ]

    for src in sources:
        rel_path = src["file_path"]
        parts.append(
            f"--- {src['name']} ({rel_path}:{src['line_start']}-{src['line_end']}) ---"
        )
        # Prefer annotated source if available
        source_text = src.get("annotated_source") or src.get("source", "")
        parts.append(f"```\n{source_text}\n```")

        if src.get("declaration_context"):
            parts.append("Referenced declarations in same file:")
            for decl in src["declaration_context"]:
                parts.append(f"  {decl}")
        parts.append("")

    return "\n".join(parts)
