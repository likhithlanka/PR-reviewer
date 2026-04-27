"""
pipeline/prompt_builder.py — Build the review prompt.

Combines: default template + language addenda + repo-level override.
"""

import logging
from pathlib import Path
from typing import Optional

from prompts.review import REVIEW_INSTRUCTIONS, REVIEW_OUTPUT_FORMAT
from prompts.lang_addenda import LANG_ADDENDA

logger = logging.getLogger(__name__)


def build_review_prompt(
    pr_title: str,
    languages: dict[str, float],
    repo_path: Optional[Path] = None,
    diff_anchors: Optional[dict[str, list[int]]] = None,
) -> str:
    """
    Build the complete review prompt.

    1. Start with the base REVIEW_INSTRUCTIONS
    2. Append language-specific addenda for detected languages (>10% of codebase)
    3. Append REVIEW_OUTPUT_FORMAT
    4. If .reviewforge/prompt.md exists in repo, append it as custom rules
    """
    prompt = REVIEW_INSTRUCTIONS

    # Language addenda
    addenda_parts = []
    for lang, proportion in sorted(languages.items(), key=lambda x: -x[1]):
        if proportion >= 0.10 and lang in LANG_ADDENDA:
            addenda_parts.append(LANG_ADDENDA[lang])

    if addenda_parts:
        prompt += "\n\n" + "\n".join(addenda_parts)

    # Inject diff_anchors data so LLM can validate findings against it
    if diff_anchors:
        anchor_summary = "\n\n### VALID DIFF ANCHORS (MANDATORY CHECK)\n"
        anchor_summary += "The following file:line combinations are the ONLY lines that exist in the diff. "
        anchor_summary += "EVERY finding you report MUST reference a line from this list. "
        anchor_summary += "If a file is not listed here, you cannot report anchored comments for it.\n\n"
        for file_path, lines in sorted(diff_anchors.items())[:50]:  # Cap to avoid massive prompts
            anchor_summary += f"- {file_path}: lines {lines}\n"
        if len(diff_anchors) > 50:
            anchor_summary += f"\n... ({len(diff_anchors) - 50} more files)\n"
        prompt += anchor_summary

    # Output format (with pr_title substituted)
    prompt += REVIEW_OUTPUT_FORMAT.format(pr_title=pr_title)

    # Repo-level custom prompt override
    if repo_path:
        custom_prompt_path = repo_path / ".reviewforge" / "prompt.md"
        if custom_prompt_path.exists():
            try:
                custom = custom_prompt_path.read_text()
                if custom.strip():
                    prompt += (
                        "\n\n### Repository-Specific Review Rules\n"
                        "The following rules are defined by the repository maintainers "
                        "in `.reviewforge/prompt.md`. Follow them with HIGH priority:\n\n"
                        + custom
                    )
                    logger.info("Loaded custom review prompt from %s", custom_prompt_path)
            except Exception as exc:
                logger.warning("Could not read custom prompt from %s: %s", custom_prompt_path, exc)

    return prompt
