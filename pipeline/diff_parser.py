"""
pipeline/diff_parser.py — Parse unified diffs to identify changed functions.

Extracts per-file changed line ranges from a unified diff, then cross-references
against the AST graph to produce a precise list of changed function/class node IDs.
"""

import logging
import re
from typing import Optional

import networkx as nx

from pipeline.utils import file_in_changeset

logger = logging.getLogger(__name__)

# Match the new-file header in unified diff: +++ b/path/to/file.py
_FILE_HEADER_RE = re.compile(r"^\+\+\+ b/(.+)$", re.M)

# Match hunk headers: @@ -old_start[,old_count] +new_start[,new_count] @@
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def parse_changed_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """
    Parse a unified diff to extract changed line ranges on the new-file side.

    Returns:
        {file_path: [(start_line, end_line), ...]}
        where start_line and end_line are 1-based inclusive line numbers.
    """
    result: dict[str, list[tuple[int, int]]] = {}
    current_file: Optional[str] = None

    for line in diff_text.splitlines():
        # Detect new file header
        file_match = _FILE_HEADER_RE.match(line)
        if file_match:
            current_file = file_match.group(1)
            if current_file not in result:
                result[current_file] = []
            continue

        # Detect hunk header
        hunk_match = _HUNK_RE.match(line)
        if hunk_match and current_file:
            start = int(hunk_match.group(1))
            count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            if count == 0:
                # Empty hunk (pure deletion on the new side) — no new lines affected
                continue
            end = start + count - 1
            result[current_file].append((start, end))

    return result


def changed_functions(
    diff_text: str,
    graph: nx.DiGraph,
) -> list[str]:
    """
    Identify which function/class nodes in the graph were actually modified
    by the diff (their line range overlaps with a changed hunk).

    Returns a list of graph node IDs.
    """
    ranges = parse_changed_ranges(diff_text)
    if not ranges:
        return []

    result: list[str] = []
    seen: set[str] = set()

    for node_id, data in graph.nodes(data=True):
        if data.get("kind") not in ("function", "class"):
            continue

        node_file = data.get("file_path", "")
        line_start = data.get("line_start")
        line_end = data.get("line_end", line_start)
        if line_start is None:
            continue

        # Find which diff file this node belongs to
        for diff_file, hunks in ranges.items():
            if not file_in_changeset(node_file, [diff_file]):
                continue
            # Check if any hunk overlaps with this node's line range
            for hunk_start, hunk_end in hunks:
                if line_start <= hunk_end and line_end >= hunk_start:
                    if node_id not in seen:
                        result.append(node_id)
                        seen.add(node_id)
                    break

    logger.info(
        "Diff analysis: %d changed hunks across %d files → %d changed functions/classes",
        sum(len(h) for h in ranges.values()),
        len(ranges),
        len(result),
    )
    return result
