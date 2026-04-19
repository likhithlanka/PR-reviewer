"""
tools/review_pr.py — 11-step pipeline orchestrator.
MCP tool: review_pr
"""

import logging
from typing import Any

from anthropic import AsyncAnthropic
from mcp.types import Tool

import config
from pipeline.cache import session
from prompts.review import REVIEW_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ReviewPRTool:
    def definition(self) -> Tool:
        return Tool(
            name="review_pr",
            description=(
                "The core 11-step pipeline orchestrator for ReviewForge. "
                "Call this first for any new PR. It will: clone/pull the repo, parse the AST, "
                "vectorize the codebase, run static analysis, perform impact/co-change analysis, "
                "fetch review history, assemble the context, and run the LLM review pass."
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
                        "description": "If true, run the pipeline to build caches but skip the final LLM call."
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
        skip_llm: bool = args.get("skip_llm", False)

        logger.info("Starting review_pr pipeline for %s/%s PR #%d", workspace, repo_slug, pr_id)
        
        # 1. Fetch PR Context (Adapter)
        logger.info("Step 1: Fetch PR Context")
        pr_data, diff = self._fetch_pr(platform, workspace, repo_slug, pr_id)
        if not pr_data or not diff:
            return f"Failed to fetch PR #{pr_id} from {platform}."

        target_branch = pr_data.get("target_branch", "master")
        source_branch = pr_data.get("source_branch", "")

        # 2. Clone/Pull Repository
        logger.info("Step 2: Clone/Pull Repo")
        from pipeline.repo_manager import RepoManager
        rm = RepoManager()
        # Checkout target branch to build base graph, then we'll analyze diff
        repo_path = rm.ensure_repo(platform, workspace, repo_slug, target_branch)
        changed_files = rm.get_changed_files(repo_path, target_branch, source_branch)
        session.set(f"pr_changed:{platform}:{workspace}:{repo_slug}", changed_files)

        # 3. Parse AST + Build Graph
        logger.info("Step 3: AST Code Graph")
        from pipeline.ast_parser import ASTParser
        parser = ASTParser()
        graph = parser.build_graph(repo_path, repo_slug, changed_files)
        session.set(f"graph:{platform}:{workspace}:{repo_slug}", parser.graph_to_serializable(graph))

        # 4. Vectorize Codebase
        logger.info("Step 4: Vectorize")
        from pipeline.vectorizer import Vectorizer
        vectorizer = Vectorizer(repo_slug)
        vectorizer.index_repo(repo_path, graph, changed_files)

        # 5. Document Fetching & Spec Search
        # (For MVP, we just do a semantic search on the PR description vs existing docs)
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
        # Trick: instantiate tool manually to reuse its summarize logic
        hist_tool = ReviewHistoryTool()
        comments = hist_tool._get_cached_history(platform, workspace, repo_slug)
        if not comments:
            comments = hist_tool._fetch_history(platform, workspace, repo_slug)
            from pipeline.cache import save_review_history
            save_review_history(repo_slug, {"comments": comments})
        
        hist_summary = ""
        if comments:
            # We filter history to the files touched in this PR to build the playbook
            filtered_comments = []
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

        if skip_llm:
            return f"Pipeline finished. Caches built. LLM review skipped. Context doc size: {len(context_doc)} chars."

        # 11. LLM Review
        logger.info("Step 11: LLM Review")
        if not config.ANTHROPIC_API_KEY:
            return "Pipeline finished up to Step 10. To run Step 11 (LLM Review), ANTHROPIC_API_KEY must be set."

        client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        try:
            resp = await client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=2048,
                system=REVIEW_SYSTEM_PROMPT.format(pr_title=pr_data.get("title", "Unknown PR")),
                messages=[{"role": "user", "content": context_doc}]
            )
            return resp.content[0].text
        except Exception as exc:
            return f"Pipeline succeeded, but LLM review failed: {exc}\n\nContext assembled successfully."

    def _fetch_pr(self, platform: str, ws: str, slug: str, pr_id: int) -> tuple[dict, str]:
        if platform == "bitbucket":
            from adapters.bitbucket import BitbucketAdapter
            adapter = BitbucketAdapter()
            return adapter.get_pr(ws, slug, pr_id), adapter.get_pr_diff(ws, slug, pr_id)
        elif platform == "github":
            from adapters.github import GitHubAdapter
            adapter = GitHubAdapter()
            return adapter.get_pr(ws, slug, pr_id), adapter.get_pr_diff(ws, slug, pr_id)
        return {}, ""
