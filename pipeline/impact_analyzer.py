"""
pipeline/impact_analyzer.py — AST graph traversal for impact analysis.

For each function/class changed in a PR diff:
  - Caller analysis: find callers NOT in the PR diff → potential breakage
  - Callee analysis: callees that also changed → combined-effect risk
  - Type flow: trace parameter/return types through changed function
  - Import analysis: validate new imports exist
  - Cross-network check: flag analogous files in sibling network dirs
"""

import logging
import re
from pathlib import Path
from typing import Any, Optional

import networkx as nx

from pipeline.utils import file_in_changeset

logger = logging.getLogger(__name__)


class ImpactAnalyzer:
    """Performs impact analysis on a code graph given a PR diff."""

    def analyze(
        self,
        graph: nx.DiGraph,
        changed_files: list[str],
        repo_path: Path,
        changed_function_nodes: Optional[list[str]] = None,
        signature_changes: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """
        Run full impact analysis on the changed files.

        Args:
            changed_function_nodes: If provided, only these specific function/class
                node IDs are analyzed for caller/callee impact (diff-level precision).
                Falls back to all functions in changed files if not provided.
            signature_changes: If provided, maps node_id → "changed" or "unchanged".
                Callers of signature-unchanged functions get "Info" severity instead
                of "Major", preventing false-positive breakage warnings.

        Returns a structured dict with all findings.
        """
        results: dict[str, Any] = {
            "caller_impact": [],
            "callee_impact": [],
            "import_issues": [],
            "cross_network_flags": [],
        }

        changed_file_set = set(changed_files)

        # Use diff-level precision when available, else fall back to file-level
        if changed_function_nodes is not None:
            changed_nodes = [
                n for n in changed_function_nodes
                if graph.has_node(n) and graph.nodes[n].get("kind") in ("function", "class")
            ]
        else:
            changed_nodes = [
                n for n, d in graph.nodes(data=True)
                if d.get("file_path")
                and file_in_changeset(d["file_path"], changed_files)
                and d.get("kind") in ("function", "class")
            ]

        # ── Caller analysis ────────────────────────────────────────────────
        for node_id in changed_nodes:
            callers = [
                u for u, v, d in graph.in_edges(node_id, data=True)
                if d.get("rel") == "called_by"
            ]
            for caller_id in callers:
                caller_data = graph.nodes.get(caller_id, {})
                caller_file = caller_data.get("file_path", "")
                in_pr = file_in_changeset(caller_file, changed_files)
                if not in_pr and caller_file:
                    # Determine severity based on whether the function's
                    # signature (parameters/return type) actually changed.
                    # Internal-only changes (body refactors, bug fixes) don't
                    # break callers, so we downgrade to Info to avoid noise.
                    sig_status = (signature_changes or {}).get(node_id, "unknown")
                    if sig_status == "unchanged":
                        severity = "Info"
                        message = (
                            f"`{node_id}` was changed (body only, signature unchanged). "
                            f"Caller `{caller_id}` (in {caller_file}) is likely unaffected, "
                            f"but verify if the behavioral change matters."
                        )
                    else:
                        severity = "Major"
                        sig_detail = " (signature changed)" if sig_status == "changed" else ""
                        message = (
                            f"`{node_id}` was changed{sig_detail} but its caller "
                            f"`{caller_id}` (in {caller_file}) was not updated."
                        )
                    results["caller_impact"].append({
                        "changed_entity": node_id,
                        "caller": caller_id,
                        "caller_file": caller_file,
                        "severity": severity,
                        "message": message,
                    })

        # ── Callee analysis ────────────────────────────────────────────────
        for node_id in changed_nodes:
            callees = [
                v for u, v, d in graph.out_edges(node_id, data=True)
                if d.get("rel") == "calls"
            ]
            for callee_id in callees:
                callee_data = graph.nodes.get(callee_id, {})
                callee_file = callee_data.get("file_path", "")
                also_changed = file_in_changeset(callee_file, changed_files)
                if also_changed:
                    results["callee_impact"].append({
                        "changed_entity": node_id,
                        "callee": callee_id,
                        "callee_file": callee_file,
                        "severity": "Minor",
                        "message": (
                            f"`{node_id}` calls `{callee_id}` which was also changed. "
                            "Verify the combined effect."
                        ),
                    })

        # ── Cross-network check ────────────────────────────────────────────
        network_dirs = self._find_network_dirs(changed_files, repo_path)
        for changed_file in changed_files:
            for net_dir, siblings in network_dirs.items():
                if net_dir in changed_file:
                    for sibling in siblings:
                        if sibling == net_dir:
                            continue
                        # Build expected sibling path
                        sibling_file = changed_file.replace(net_dir, sibling)
                        if sibling_file not in changed_file_set:
                            sibling_path = repo_path / sibling_file
                            if sibling_path.exists():
                                results["cross_network_flags"].append({
                                    "changed_file": changed_file,
                                    "missing_sibling": sibling_file,
                                    "severity": "Major",
                                    "message": (
                                        f"Change in `{changed_file}` but analogous file "
                                        f"`{sibling_file}` was NOT updated. "
                                        "Cross-network parity issue."
                                    ),
                                })

        # ── Import analysis ────────────────────────────────────────────────
        results["import_issues"] = self._check_imports(graph, changed_files, repo_path)

        return results

    def _find_network_dirs(self, changed_files: list[str], repo_path: Optional[Path] = None) -> dict[str, list[str]]:
        """
        Detect sibling network directories (e.g. Network/Visa, Network/Mastercard).
        Returns {changed_net_dir → [all_sibling_dirs_on_disk]}.

        Scans the filesystem so that siblings NOT in the PR diff are still found.
        Falls back to PR-diff-only detection if repo_path is not provided.
        """
        network_pattern = re.compile(r"((?:Network|network|networks)/[^/]+)/")

        # Find which network dirs are touched in this PR
        touched: dict[str, str] = {}  # net_dir → parent
        for f in changed_files:
            m = network_pattern.search(f)
            if m:
                net_dir = m.group(1)
                parent = net_dir.rsplit("/", 1)[0] if "/" in net_dir else ""
                touched[net_dir] = parent

        if not touched:
            return {}

        result: dict[str, list[str]] = {}

        for net_dir, parent in touched.items():
            if repo_path:
                # Scan the actual parent directory on disk to find all siblings
                parent_path = repo_path / parent if parent else repo_path
                try:
                    siblings = [
                        (parent + "/" + d.name if parent else d.name)
                        for d in parent_path.iterdir()
                        if d.is_dir()
                    ]
                except OSError:
                    siblings = list(touched.keys())
            else:
                siblings = list(touched.keys())

            result[net_dir] = siblings

        return result

    def _check_imports(
        self,
        graph: nx.DiGraph,
        changed_files: list[str],
        repo_path: Path,
    ) -> list[dict]:
        """Check that new imports in changed files resolve to existing modules."""
        issues = []
        for node_id, data in graph.nodes(data=True):
            if data.get("kind") != "module":
                continue
            node_file = data.get("file_path", "")
            if not file_in_changeset(node_file, changed_files):
                continue
            for _, imported_mod, edge_data in graph.out_edges(node_id, data=True):
                if edge_data.get("rel") != "imports":
                    continue
                # Only check dotted imports that look local (not stdlib/third-party).
                # Heuristic: skip single-segment imports (e.g. "os", "json") and
                # known third-party prefixes; flag dotted names whose first segment
                # doesn't correspond to any top-level directory in the repo.
                parts = imported_mod.split(".")
                if len(parts) < 2:
                    continue  # single-segment → almost certainly stdlib/third-party
                top_level = parts[0]
                # If the top-level package directory doesn't exist in the repo, it's
                # an external import — skip it.
                if not (repo_path / top_level).exists() and not (repo_path / (top_level + ".py")).exists():
                    continue
                mod_as_path = repo_path / (imported_mod.replace(".", "/") + ".py")
                mod_as_hs = repo_path / (imported_mod.replace(".", "/") + ".hs")
                if not mod_as_path.exists() and not mod_as_hs.exists():
                    issues.append({
                        "changed_file": node_file,
                        "import": imported_mod,
                        "severity": "Major",
                        "message": (
                            f"`{node_file}` imports `{imported_mod}` which does not "
                            f"resolve to a local module. File may be missing or the "
                            f"import path is incorrect."
                        ),
                    })
        return issues

    def get_entity_impact(
        self,
        graph: nx.DiGraph,
        entity_name: str,
        pr_changed_files: list[str],
    ) -> dict[str, Any]:
        """
        Interactive mode: return callers, callees, and type info for one entity.
        """
        matching = [n for n in graph.nodes if entity_name in n]
        if not matching:
            return {"error": f"No entity found matching '{entity_name}'"}

        node_id = matching[0]
        data = dict(graph.nodes[node_id])

        callers = [
            {"id": u, "file": graph.nodes.get(u, {}).get("file_path", ""),
             "in_pr": file_in_changeset(graph.nodes.get(u, {}).get("file_path", ""), pr_changed_files)}
            for u, _, d in graph.in_edges(node_id, data=True) if d.get("rel") == "called_by"
        ]
        callees = [
            {"id": v, "file": graph.nodes.get(v, {}).get("file_path", "")}
            for _, v, d in graph.out_edges(node_id, data=True) if d.get("rel") == "calls"
        ]

        return {
            "entity": node_id,
            "metadata": data,
            "callers": callers,
            "callees": callees,
            "unupdated_callers": [c for c in callers if not c["in_pr"]],
        }
