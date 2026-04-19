"""
pipeline/language_detector.py — Detect language mix from changed file extensions.

Reuses the _LANG_MAP from ast_parser to avoid duplication.
"""

from pathlib import Path

from pipeline.ast_parser import _LANG_MAP


def detect_languages(changed_files: list[str]) -> dict[str, float]:
    """
    Return language -> proportion mapping based on file extensions.
    Only includes languages with >0% presence among recognized files.
    """
    counts: dict[str, int] = {}
    total = 0
    for f in changed_files:
        ext = Path(f).suffix.lower()
        lang = _LANG_MAP.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
            total += 1
    if total == 0:
        return {}
    return {lang: count / total for lang, count in sorted(counts.items(), key=lambda x: -x[1])}
