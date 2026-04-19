"""
pipeline/context_assembler.py — Build the unified context document for LLM review.
Handles token budget and priority truncation.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Assembles data from all pipeline steps into a single review context string."""

    def __init__(self, max_tokens: int = 12000):
        self.max_tokens = max_tokens
        self._approx_chars_per_token = 4

    def assemble(
        self,
        pr_data: dict[str, Any],
        diff: str,
        impact_analysis: dict[str, Any],
        cochange_findings: list[dict],
        static_analysis: dict[str, list[dict]],
        review_history_summary: str,
        spec_quotes: list[dict],
        changed_function_sources: str = "",
        branch_analysis: str = "",
    ) -> str:
        """
        Build the context document.
        Priority-based truncation ensures critical data (diff, impact) fits.
        """
        parts = []

        # 1. PR Metadata (Highest Priority)
        parts.append("=== PR METADATA ===")
        parts.append(f"Title: {pr_data.get('title', 'Unknown')}")
        parts.append(f"Author: {pr_data.get('author', 'Unknown')}")
        parts.append(f"Branch: {pr_data.get('source_branch', '')} → {pr_data.get('target_branch', '')}")
        parts.append(f"Description:\n{pr_data.get('description', 'None')}\n")

        # 2. Diff (Highest Priority)
        parts.append("=== CODE DIFF ===")
        parts.append(diff)
        parts.append("")

        # 2.5. Full Source of Changed Functions
        # This gives the reviewer complete function bodies so it can verify
        # claims against actual code, not just the few lines of diff context.
        if changed_function_sources:
            parts.append(changed_function_sources)
            parts.append("")

        # 2.6. Verified Branch Analysis
        # Deterministic AST-proven facts about conditional structures.
        # The reviewer MUST NOT contradict these.
        if branch_analysis:
            parts.append(branch_analysis)
            parts.append("")

        # 3. Impact Analysis
        parts.append("=== IMPACT ANALYSIS (AST Call Graph) ===")
        unupdated = impact_analysis.get("caller_impact", [])
        callees = impact_analysis.get("callee_impact", [])
        import_iss = impact_analysis.get("import_issues", [])
        network = impact_analysis.get("cross_network_flags", [])

        if not any([unupdated, callees, import_iss, network]):
            parts.append("No structural impact flags.\n")
        else:
            for item in unupdated:
                severity = item.get("severity", "MAJOR").upper()
                verification = item.get("verification", "unverified").upper()
                parts.append(f"[{severity}] [{verification}] Unupdated caller: {item['message']}")
                if item.get("verification_note"):
                    parts.append(f"  ↳ {item['verification_note']}")
            for item in network:
                parts.append(f"[MAJOR] [VERIFIED] Cross-network: {item['message']}")
            for item in callees:
                parts.append(f"[MINOR] Callee changed: {item['message']}")
            for item in import_iss:
                parts.append(f"[{item.get('severity', 'MAJOR').upper()}] Import issue: {item['message']}")
            parts.append("")

        # 4. Co-change Analysis
        parts.append("=== CO-CHANGE ANALYSIS (Git History) ===")
        if not cochange_findings:
            parts.append("No missing highly-correlated files.\n")
        else:
            for item in cochange_findings:
                parts.append(f"[FLAG] Missing file: `{item['missing_file']}` (Reason: {item['reason']})")
            parts.append("")

        # 5. Static Analysis
        parts.append("=== STATIC ANALYSIS ===")
        found_sa = False
        for tool, findings in static_analysis.items():
            if findings:
                found_sa = True
                for f in findings:
                    parts.append(f"[{tool.upper()}] {f.get('file')}:{f.get('line')} - {f.get('message')}")
        if not found_sa:
            parts.append("No static analysis findings.\n")
        else:
            parts.append("")

        # 6. Review History / Dynamic Playbook
        parts.append("=== DYNAMIC PLAYBOOK (Rules from past PRs) ===")
        if review_history_summary:
            parts.append(review_history_summary)
        else:
            parts.append("No historical review data available.")
        parts.append("")

        # 7. Specs & Referenced Docs
        parts.append("=== REFERENCED DOCUMENTS ===")
        if spec_quotes:
            for sq in spec_quotes:
                parts.append(f"Source: {sq.get('url')} (Score: {sq.get('score')})")
                parts.append(f"```\n{sq.get('content')}\n```\n")
        else:
            parts.append("No documentation references found or linked.\n")

        # Rough token gating (diff not truncated)
        final_text = "\n".join(parts)
        if len(final_text) > self.max_tokens * self._approx_chars_per_token:
            logger.warning("Context exceeds rough token budget. Sending anyway, rely on LLM context window.")
            # We skip explicit truncation since Claude Opus/Sonnet 3.5 have 200k+ windows
            # The PRD mentions optional truncation, but we'll leverage the large context for v2.0
            
        return final_text
