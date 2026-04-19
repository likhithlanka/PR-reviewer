"""
tools/review_pr.py — 11-step pipeline orchestrator.
MCP tool: review_pr

When skip_llm=True or ANTHROPIC_API_KEY is missing, returns a structured
JSON payload with all pipeline data + a composable review prompt so the
calling agent can perform the review itself.
"""

import json
import logging
from typing import Any

from mcp.types import Tool

import config
from pipeline.cache import session

logger = logging.getLogger(__name__)


class ReviewPRTool:
    def definition(self) -> Tool:
        return Tool(
            name="review_pr",
            description=(
                "The core pipeline orchestrator for ReviewForge. "
                "Call this first for any new PR. It will: clone/pull the repo, parse the AST, "
                "vectorize the codebase, run static analysis, perform impact/co-change analysis, "
                "fetch review history, and assemble the context. "
                "When agent_review=True (or ANTHROPIC_API_KEY is missing), returns structured "
                "JSON with pipeline data + review prompt for agent-driven review. "
                "When agent_review=False and ANTHROPIC_API_KEY is set, runs the LLM review internally."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "default": "bitbucket"},
                    "workspace": {"type": "string"},
                    "repo_slug": {"type": "string"},
                    "pr_id": {"type": "integer"},
                    "skip_llm": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, skip the internal LLM call and return structured pipeline data."
                    },
                    "agent_review": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, return structured pipeline data for agent-driven review instead of calling LLM internally."
                    }
                },
                "required": ["workspace", "repo_slug", "pr_id"],
            },
        )

    async def run(self, args: dict) -> str:
        platform: str = args.get("platform", "bitbucket")
        workspace: str = args["workspace"]
        repo_slug: str = args["repo_slug"]
        pr_id: int = int(args["pr_id"])
        skip_llm: bool = args.get("skip_llm", False) or args.get("agent_review", False)

        logger.info("Starting review_pr pipeline for %s/%s PR #%d", workspace, repo_slug, pr_id)

        # 1. Fetch PR Context (Adapter)
        logger.info("Step 1: Fetch PR Context")
        pr_data, diff, fetch_errors = self._fetch_pr(platform, workspace, repo_slug, pr_id)
        if not pr_data or not diff:
            error_detail = "\n".join(f"  - {e}" for e in fetch_errors)
            return (
                f"Failed to fetch PR #{pr_id} from {platform}/{workspace}/{repo_slug}.\n\n"
                f"Errors:\n{error_detail}\n\n"
                f"Troubleshooting:\n"
                f"  1. Check BITBUCKET_TOKEN is set and valid in your MCP server env config.\n"
                f"  2. If using Bitbucket Data Center, ensure BITBUCKET_URL is set (e.g. https://bitbucket.juspay.net).\n"
                f"     Without it, ReviewForge defaults to Cloud mode (api.bitbucket.org) which will reject Data Center tokens.\n"
                f"  3. For Data Center, the workspace parameter should be the project KEY (e.g. 'EXC'), not the display name.\n"
                f"  4. Verify the repo slug and PR ID exist and the token has access."
            )

        target_branch = pr_data.get("target_branch", "master")
        source_branch = pr_data.get("source_branch", "")

        # 2. Clone/Pull Repository
        logger.info("Step 2: Clone/Pull Repo")
        from pipeline.repo_manager import RepoManager
        rm = RepoManager()
        repo_path = rm.ensure_repo(platform, workspace, repo_slug, target_branch)
        changed_files = rm.get_changed_files(repo_path, target_branch, source_branch)
        session.set(f"pr_changed:{platform}:{workspace}:{repo_slug}", changed_files)

        # 3. Parse AST + Build Graph
        logger.info("Step 3: AST Code Graph")
        from pipeline.ast_parser import ASTParser
        parser = ASTParser()
        graph = parser.build_graph(repo_path, repo_slug, changed_files)
        session.set(f"graph:{platform}:{workspace}:{repo_slug}", parser.graph_to_serializable(graph))

        # 3.5. Detect Languages
        from pipeline.language_detector import detect_languages
        languages = detect_languages(changed_files)
        logger.info("Detected languages: %s", languages)

        # 4. Vectorize Codebase
        logger.info("Step 4: Vectorize")
        from pipeline.vectorizer import Vectorizer
        vectorizer = Vectorizer(repo_slug)
        vectorizer.index_repo(repo_path, graph, changed_files)

        # 5. Document Fetching & Spec Search
        logger.info("Step 5: Document Processing")
        spec_quotes = []
        if pr_data.get("description"):
            spec_quotes = vectorizer.search(pr_data["description"], n_results=3, collection="docs")

        # 6. Impact Analysis (AST)
        logger.info("Step 6: Impact Analysis")
        from pipeline.impact_analyzer import ImpactAnalyzer
        impact_analyzer = ImpactAnalyzer()
        impact_findings = impact_analyzer.analyze(graph, changed_files, repo_path)

        # 7. Co-Change Analysis
        logger.info("Step 7: Co-Change Analysis")
        from pipeline.cochange_analyzer import CoChangeAnalyzer
        cochange_findings = CoChangeAnalyzer().analyze(repo_path, changed_files)

        # 8. Static Analysis
        logger.info("Step 8: Static Analysis")
        from pipeline.static_analyzer import StaticAnalyzer
        static_findings = StaticAnalyzer().run_all(repo_path, changed_files)

        # 9. Review History
        logger.info("Step 9: Review History")
        from tools.review_history import ReviewHistoryTool
        hist_tool = ReviewHistoryTool()
        comments = hist_tool._get_cached_history(platform, workspace, repo_slug)
        if not comments:
            comments = hist_tool._fetch_history(platform, workspace, repo_slug)
            from pipeline.cache import save_review_history
            save_review_history(repo_slug, {"comments": comments})

        hist_summary = ""
        filtered_comments = []
        if comments:
            for c in comments:
                if any(cf in c.get("file_path", "") for cf in changed_files):
                    filtered_comments.append(c)
            if filtered_comments:
                hist_summary = await hist_tool._summarize(filtered_comments, "Changed files in PR")

        # 10. Assemble Context
        logger.info("Step 10: Assemble Context")
        from pipeline.context_assembler import ContextAssembler
        assembler = ContextAssembler()
        context_doc = assembler.assemble(
            pr_data=pr_data,
            diff=diff,
            impact_analysis=impact_findings,
            cochange_findings=cochange_findings,
            static_analysis=static_findings,
            review_history_summary=hist_summary,
            spec_quotes=spec_quotes,
        )

        session.set_pr_context(platform, workspace, repo_slug, str(pr_id), {
            "changed_files": changed_files,
            "context_doc": context_doc,
        })

        # Build the composed review prompt
        from pipeline.prompt_builder import build_review_prompt
        review_prompt = build_review_prompt(
            pr_title=pr_data.get("title", "Unknown PR"),
            languages=languages,
            repo_path=repo_path,
        )

        if skip_llm or not config.ANTHROPIC_API_KEY:
            return self._build_payload(
                pr_data=pr_data,
                changed_files=changed_files,
                languages=languages,
                impact_findings=impact_findings,
                cochange_findings=cochange_findings,
                static_findings=static_findings,
                filtered_comments=filtered_comments,
                context_doc=context_doc,
                review_prompt=review_prompt,
                platform=platform,
                workspace=workspace,
                repo_slug=repo_slug,
                pr_id=pr_id,
            )

        # 11. LLM Review
        logger.info("Step 11: LLM Review")
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        try:
            resp = await client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=2048,
                system=review_prompt,
                messages=[{"role": "user", "content": context_doc}]
            )
            return resp.content[0].text
        except Exception as exc:
            return f"Pipeline succeeded, but LLM review failed: {exc}\n\nContext assembled successfully."

    def _build_payload(
        self,
        pr_data: dict,
        changed_files: list[str],
        languages: dict[str, float],
        impact_findings: dict,
        cochange_findings: list[dict],
        static_findings: dict,
        filtered_comments: list[dict],
        context_doc: str,
        review_prompt: str,
        platform: str,
        workspace: str,
        repo_slug: str,
        pr_id: int,
    ) -> str:
        """Build the structured JSON payload for agent-driven review."""
        # Truncate impact findings
        impact_summary = []
        for key in ("caller_impact", "callee_impact", "cross_network_flags", "import_issues"):
            for item in impact_findings.get(key, [])[:config.PAYLOAD_MAX_IMPACT_ITEMS]:
                impact_summary.append({
                    "type": key,
                    "severity": item.get("severity", "info"),
                    "message": item.get("message", "")[:200],
                    "file": item.get("caller_file") or item.get("changed_file") or item.get("callee_file", ""),
                })

        # Truncate co-change findings
        cochange_summary = [
            {
                "missing_file": f["missing_file"],
                "reason": f["reason"][:200],
                "co_change_frequency": f.get("co_change_frequency"),
            }
            for f in cochange_findings[:config.PAYLOAD_MAX_COCHANGE_ITEMS]
        ]

        # Static analysis: counts + top severe
        static_summary = {}
        for tool, findings in static_findings.items():
            static_summary[tool] = {
                "total_findings": len(findings),
                "top_findings": [
                    {
                        "file": f.get("file"),
                        "line": f.get("line"),
                        "message": f.get("message", "")[:150],
                    }
                    for f in sorted(
                        findings,
                        key=lambda x: 0 if x.get("severity") == "error" else 1,
                    )[:config.PAYLOAD_MAX_STATIC_TOP]
                ],
            }

        # Context doc preview
        preview = context_doc[:config.CONTEXT_DOC_PREVIEW_CHARS]
        if len(context_doc) > config.CONTEXT_DOC_PREVIEW_CHARS:
            preview += f"\n\n... [truncated, full context_doc is {len(context_doc)} chars. Use search/read_file tools for details]"

        # Raw review history comments (for agent to generate its own playbook)
        raw_comments = [
            {
                "file_path": c.get("file_path", ""),
                "content": c.get("content", "")[:300],
                "author": c.get("author", ""),
            }
            for c in filtered_comments[:20]
        ]

        payload = {
            "status": "pipeline_complete",
            "pr_metadata": pr_data,
            "changed_files": changed_files,
            "languages": languages,
            "impact_summary": impact_summary,
            "cochange_summary": cochange_summary,
            "static_summary": static_summary,
            "review_history_comments": raw_comments,
            "context_doc_preview": preview,
            "context_doc_total_chars": len(context_doc),
            "review_prompt": review_prompt,
            "tool_hints": {
                "deep_dive_impact": (
                    f"Call `impact_analysis` with an entity name (function or file path) "
                    f"to see callers/callees and unupdated callers. "
                    f"Example: impact_analysis(entity='check_hsbconus_cycles', workspace='{workspace}', repo_slug='{repo_slug}')"
                ),
                "read_source": (
                    f"Call `read_file` with a file_path to view source with optional AST context and git history. "
                    f"Example: read_file(file_path='src/foo.py', workspace='{workspace}', repo_slug='{repo_slug}', include_ast_context=True)"
                ),
                "search_codebase": (
                    f"Call `search` with a natural language query to semantically search code and docs. "
                    f"Example: search(query='email notification logic', workspace='{workspace}', repo_slug='{repo_slug}')"
                ),
                "run_static": (
                    f"Call `run_analysis` with tool_name to run a specific static analyzer. "
                    f"Example: run_analysis(tool_name='ruff', workspace='{workspace}', repo_slug='{repo_slug}')"
                ),
                "check_history": (
                    f"Call `get_review_history` with file_path to see past review comments for that file. "
                    f"Example: get_review_history(workspace='{workspace}', repo_slug='{repo_slug}', file_path='src/foo.py')"
                ),
                "view_pr_comments": (
                    f"Call `get_pr_comments` with pr_id to see existing comments on the PR. "
                    f"Example: get_pr_comments(pr_id={pr_id}, workspace='{workspace}', repo_slug='{repo_slug}')"
                ),
                "anti_false_positive_rules": (
                    "CRITICAL — Follow these rules to avoid false positives:\n"
                    "1. Diff-first: Only report changes visible in the diff (lines with -/+ prefixes). "
                    "PR descriptions are unreliable — never infer changes from them.\n"
                    "2. Trace before flagging: When X is missing but present elsewhere, trace the code path "
                    "to understand WHY before calling it a bug. Use `read_file` with `include_ast_context=True` "
                    "or `impact_analysis` to verify.\n"
                    "3. Verify control flow: Before claiming a line always/never executes, trace indentation "
                    "and conditional structure. Use `read_file` to see the actual code, not just the diff context.\n"
                    "4. Observations ≠ findings: 'These two methods differ in decorator usage' is an observation. "
                    "It becomes a finding only after tracing proves the difference is unintentional."
                ),
            },
        }

        return json.dumps(payload, indent=2)

    def _fetch_pr(self, platform: str, ws: str, slug: str, pr_id: int) -> tuple[dict, str, list[str]]:
        """Returns (pr_data, diff, errors). errors is a list of human-readable error strings."""
        errors: list[str] = []
        pr_data: dict = {}
        diff: str = ""

        if platform == "bitbucket":
            from adapters.bitbucket import BitbucketAdapter
            adapter = BitbucketAdapter()

            pr_data, pr_err = adapter.get_pr(ws, slug, pr_id)
            if pr_err:
                errors.append(f"[get_pr] {pr_err}")

            diff, diff_err = adapter.get_pr_diff(ws, slug, pr_id)
            if diff_err:
                errors.append(f"[get_pr_diff] {diff_err}")

        elif platform == "github":
            from adapters.github import GitHubAdapter
            adapter = GitHubAdapter()
            pr_data = adapter.get_pr(ws, slug, pr_id)
            diff = adapter.get_pr_diff(ws, slug, pr_id)

        if not pr_data:
            errors.append(
                f"PR data is empty for {platform}/{ws}/{slug}#{pr_id}. "
                f"Verify the workspace/project key, repo slug, and PR ID are correct."
            )
        if not diff:
            errors.append(
                f"Diff is empty for {platform}/{ws}/{slug}#{pr_id}. "
                f"The PR may not have any changes, or the diff endpoint failed."
            )

        return pr_data, diff, errors
