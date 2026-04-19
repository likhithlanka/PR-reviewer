"""
pipeline/cochange_analyzer.py — Git log co-change analysis.

For each changed file in a PR, compute how often it was co-committed
with other files historically. Flag missing highly-correlated files.
"""

import logging
import time
from collections import defaultdict
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class CoChangeAnalyzer:
    def analyze(self, repo_path: Path, changed_files: list[str]) -> list[dict]:
        """
        Analyze git history to find missing files that are usually 
        changed together with the files in the PR diff.
        """
        import git
        
        try:
            repo = git.Repo(str(repo_path))
        except Exception as exc:
            logger.warning("Co-change analysis failed to open repo: %s", exc)
            return []

        # Lookback timestamp
        since = int(time.time()) - (config.COCHANGE_LOOKBACK_MONTHS * 30 * 86400)
        since_date = time.strftime("%Y-%m-%d", time.gmtime(since))

        # We need commit history to compute frequencies.
        # file_commits: mapping of file -> set(commit_hexshas)
        file_commits: dict[str, set[str]] = defaultdict(set)
        
        try:
            logger.info("Computing git co-change frequencies since %s", since_date)
            # Fetch all commits in the lookback period
            for commit in repo.iter_commits(since=since_date):
                # Parents logic: check diff against first parent
                if not commit.parents:
                    continue
                diffs = commit.parents[0].diff(commit)
                commit_files = [d.b_path for d in diffs if d.b_path]
                for f in commit_files:
                    file_commits[f].add(commit.hexsha)
        except Exception as exc:
            logger.warning("Error reading git logs: %s", exc)
            return []

        missing_flags = []
        changed_set = set(changed_files)

        for pr_file in changed_files:
            commits_touching_pr_file = file_commits.get(pr_file, set())
            total_commits = len(commits_touching_pr_file)
            
            # Need a minimum threshold of history to be statistically relevant
            if total_commits < 3:
                continue

            # Compare with every other file touched in those same commits
            co_counts: dict[str, int] = defaultdict(int)
            for f, f_commits in file_commits.items():
                if f == pr_file or f in changed_set:
                    continue
                overlap = commits_touching_pr_file.intersection(f_commits)
                if overlap:
                    co_counts[f] = len(overlap)

            for associated_file, count in co_counts.items():
                frequency = count / total_commits
                if frequency >= config.COCHANGE_THRESHOLD:
                    reason = ""
                    # Business logic / special cases
                    if "models.py" in pr_file and "migrations/" in associated_file:
                        reason = "DB model was changed, but the associated migration file is missing."
                    elif frequency > 0.8:
                        reason = f"Highly correlated: changes with `{pr_file}` {frequency*100:.0f}% of the time."
                    else:
                        reason = f"Historically changes with `{pr_file}`."

                    missing_flags.append({
                        "pr_file": pr_file,
                        "missing_file": associated_file,
                        "co_change_frequency": round(frequency, 2),
                        "shared_commits": count,
                        "reason": reason,
                    })

        # Deduplicate recommendations
        unique_missing = {f["missing_file"]: f for f in missing_flags}
        return sorted(list(unique_missing.values()), key=lambda x: x["co_change_frequency"], reverse=True)
