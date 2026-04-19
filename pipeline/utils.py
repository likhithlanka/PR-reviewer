"""
pipeline/utils.py — Shared helpers for the pipeline.
"""

from pathlib import PurePosixPath


def _file_matches(node_path: str, changed_file: str) -> bool:
    """
    True if changed_file matches node_path by full path-component suffix.

    Examples:
        _file_matches("/repo/src/utils.py", "utils.py")       → True
        _file_matches("/repo/src/utils.py", "src/utils.py")    → True
        _file_matches("/repo/test_utils.py", "utils.py")       → False
        _file_matches("/repo/utils.py.bak", "utils.py")        → False
    """
    np_parts = PurePosixPath(node_path).parts
    cp_parts = PurePosixPath(changed_file).parts
    if not cp_parts or len(cp_parts) > len(np_parts):
        return False
    return np_parts[-len(cp_parts):] == cp_parts


def file_in_changeset(node_path: str, changed_files: list[str]) -> bool:
    """True if node_path matches any file in changed_files by path-component suffix."""
    if not node_path:
        return False
    return any(_file_matches(node_path, cf) for cf in changed_files)
