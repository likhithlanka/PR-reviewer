"""
pipeline/ast_parser.py — AST parsing and code graph construction.

Builds a NetworkX directed graph from the codebase:
  Nodes: function, class, module, variable
  Edges: calls, called_by, imports, imported_by, inherits, used_by

Language support:
  Python:          stdlib `ast` module (no dependencies)
  Haskell:         tree-sitter-haskell
  JS/TS:           tree-sitter-javascript / tree-sitter-typescript
  Generic fallback: tree-sitter with language auto-detection → regex

Graph is serialized to ~/.reviewforge/graphs/{slug}.json
"""

import ast as py_ast
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional, Union

import networkx as nx

from pipeline.cache import load_graph, save_graph

logger = logging.getLogger(__name__)

# Language detection by extension
_LANG_MAP = {
    ".py": "python",
    ".hs": "haskell",
    ".lhs": "haskell",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


# ── Parser implementations ─────────────────────────────────────────────────

class _PythonParser:
    """Parse Python files using stdlib ast module."""

    def parse(self, file_path: Path, content: str, graph: nx.DiGraph) -> None:
        try:
            tree = py_ast.parse(content, filename=str(file_path))
        except SyntaxError as exc:
            logger.warning("Python parse error %s: %s", file_path, exc)
            return

        module_id = str(file_path)
        graph.add_node(module_id, kind="module", file_path=str(file_path))

        visitor = _PythonVisitor(file_path, graph, module_id)
        visitor.visit(tree)


class _PythonVisitor(py_ast.NodeVisitor):
    def __init__(self, file_path: Path, graph: nx.DiGraph, module_id: str) -> None:
        self.file_path = file_path
        self.graph = graph
        self.module_id = module_id
        self._scope: list[str] = [module_id]

    def _current_scope(self) -> str:
        return self._scope[-1]

    def visit_FunctionDef(self, node: py_ast.FunctionDef) -> None:  # noqa: N802
        self._add_function(node)

    def visit_AsyncFunctionDef(self, node: py_ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._add_function(node)

    def _add_function(self, node: Union[py_ast.FunctionDef, py_ast.AsyncFunctionDef]) -> None:
        func_id = f"{self.module_id}::{node.name}"
        args = [a.arg for a in node.args.args]
        returns = py_ast.unparse(node.returns) if node.returns else None
        decorators = [py_ast.unparse(d) for d in node.decorator_list]
        self.graph.add_node(
            func_id,
            kind="function",
            name=node.name,
            file_path=str(self.file_path),
            line_start=node.lineno,
            line_end=node.end_lineno,
            parameters=args,
            return_type=returns,
            decorators=decorators,
        )
        self.graph.add_edge(self.module_id, func_id, rel="contains")
        self.graph.add_edge(func_id, self.module_id, rel="defined_in")

        # Record calls inside the function body
        self._scope.append(func_id)
        for child in py_ast.walk(node):
            if isinstance(child, py_ast.Call):
                callee_name = self._resolve_call(child)
                if callee_name:
                    self.graph.add_edge(func_id, callee_name, rel="calls")
                    self.graph.add_edge(callee_name, func_id, rel="called_by")
        self._scope.pop()

        self.generic_visit(node)

    def _resolve_call(self, node: py_ast.Call) -> Optional[str]:
        func = node.func
        if isinstance(func, py_ast.Name):
            return func.id
        if isinstance(func, py_ast.Attribute):
            return func.attr
        return None

    def visit_ClassDef(self, node: py_ast.ClassDef) -> None:  # noqa: N802
        class_id = f"{self.module_id}::{node.name}"
        bases = [py_ast.unparse(b) for b in node.bases]
        self.graph.add_node(
            class_id,
            kind="class",
            name=node.name,
            file_path=str(self.file_path),
            line_start=node.lineno,
            line_end=node.end_lineno,
            bases=bases,
        )
        self.graph.add_edge(self.module_id, class_id, rel="contains")
        for base in bases:
            self.graph.add_edge(class_id, base, rel="inherits")
        self.generic_visit(node)

    def visit_Import(self, node: py_ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.graph.add_edge(self.module_id, alias.name, rel="imports")

    def visit_ImportFrom(self, node: py_ast.ImportFrom) -> None:  # noqa: N802
        if node.module:
            self.graph.add_edge(self.module_id, node.module, rel="imports")


class _TreeSitterParser:
    """Parse Haskell / JS / TS using tree-sitter."""

    def parse(self, file_path: Path, content: str, graph: nx.DiGraph, language: str) -> None:
        try:
            import tree_sitter_python  # noqa (just to check tree-sitter is installed)
            from tree_sitter import Language, Parser
        except ImportError:
            logger.warning("tree-sitter not available, falling back to regex for %s", file_path)
            _RegexFallbackParser().parse(file_path, content, graph)
            return

        try:
            if language == "haskell":
                import tree_sitter_haskell as ts_lang
            elif language == "javascript":
                import tree_sitter_javascript as ts_lang
            elif language == "typescript":
                import tree_sitter_typescript as ts_lang
                ts_lang = ts_lang.typescript  # type: ignore[attr-defined]
            else:
                logger.warning("Unsupported tree-sitter language: %s", language)
                _RegexFallbackParser().parse(file_path, content, graph)
                return

            lang = Language(ts_lang.language())
            parser = Parser(lang)
            tree = parser.parse(content.encode())
        except Exception as exc:
            logger.warning("tree-sitter parse error %s: %s", file_path, exc)
            _RegexFallbackParser().parse(file_path, content, graph)
            return

        module_id = str(file_path)
        graph.add_node(module_id, kind="module", file_path=str(file_path), language=language)

        # Walk tree for function definitions
        self._walk(tree.root_node, content, file_path, module_id, graph, language)

    def _walk(self, node, content: str, file_path: Path, module_id: str, graph: nx.DiGraph, language: str) -> None:
        fn_types = {
            "haskell": ["function_declaration", "signature"],
            "javascript": ["function_declaration", "arrow_function", "method_definition"],
            "typescript": ["function_declaration", "arrow_function", "method_definition"],
        }
        target_types = fn_types.get(language, [])

        if node.type in target_types:
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode() if name_node else f"anon_{node.start_point[0]}"
            func_id = f"{module_id}::{name}"
            graph.add_node(
                func_id,
                kind="function",
                name=name,
                file_path=str(file_path),
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                language=language,
            )
            graph.add_edge(module_id, func_id, rel="contains")
            graph.add_edge(func_id, module_id, rel="defined_in")

        for child in node.children:
            self._walk(child, content, file_path, module_id, graph, language)


class _RegexFallbackParser:
    """Generic regex-based function/class extraction for unsupported languages."""

    _FN_PATTERNS = [
        re.compile(r"^(?:def|function|func|fn|sub)\s+(\w+)", re.M),
        re.compile(r"^(\w+)\s*::\s*.+->", re.M),  # Haskell type signature
        re.compile(r"^(\w+)\s+\w+\s*=", re.M),    # Haskell definition
    ]

    def parse(self, file_path: Path, content: str, graph: nx.DiGraph) -> None:
        module_id = str(file_path)
        graph.add_node(module_id, kind="module", file_path=str(file_path))
        for pattern in self._FN_PATTERNS:
            for m in pattern.finditer(content):
                name = m.group(1)
                lineno = content[: m.start()].count("\n") + 1
                func_id = f"{module_id}::{name}"
                if not graph.has_node(func_id):
                    graph.add_node(
                        func_id,
                        kind="function",
                        name=name,
                        file_path=str(file_path),
                        line_start=lineno,
                    )
                    graph.add_edge(module_id, func_id, rel="contains")


# ── Main ASTParser class ───────────────────────────────────────────────────

class ASTParser:
    """
    Top-level parser. Dispatches to the correct language parser.
    Manages a NetworkX graph and supports incremental re-parsing.
    """

    def __init__(self) -> None:
        self._python = _PythonParser()
        self._treesitter = _TreeSitterParser()
        self._regex = _RegexFallbackParser()

    def build_graph(
        self,
        repo_path: Path,
        repo_slug: str,
        changed_files: Optional[list[str]] = None,
    ) -> nx.DiGraph:
        """
        Build (or incrementally update) the code graph for a repo.
        If changed_files is provided, only re-parse those files.
        """
        # Load existing graph
        cached = load_graph(repo_slug)
        if cached:
            graph = nx.node_link_graph(cached)
            logger.info("Loaded existing graph for %s (%d nodes)", repo_slug, len(graph.nodes))
        else:
            graph = nx.DiGraph()
            logger.info("Building fresh graph for %s", repo_slug)

        files_to_parse: list[Path]
        if changed_files:
            files_to_parse = [repo_path / f for f in changed_files if (repo_path / f).exists()]
            # Remove stale nodes for changed files
            for fpath in files_to_parse:
                stale = [n for n, d in graph.nodes(data=True) if d.get("file_path") == str(fpath)]
                graph.remove_nodes_from(stale)
        else:
            files_to_parse = self._collect_files(repo_path)

        logger.info("Parsing %d files…", len(files_to_parse))
        for fpath in files_to_parse:
            self._parse_file(fpath, graph)

        save_graph(repo_slug, nx.node_link_data(graph))
        logger.info("Graph built: %d nodes, %d edges", len(graph.nodes), len(graph.edges))
        return graph

    def _collect_files(self, repo_path: Path) -> list[Path]:
        exts = set(_LANG_MAP.keys())
        files = []
        for p in repo_path.rglob("*"):
            if p.suffix in exts and p.is_file():
                # Exclude vendor / generated dirs
                parts = set(p.parts)
                if parts & {"node_modules", ".stack-work", "dist", "dist-newstyle", "__pycache__"}:
                    continue
                files.append(p)
        return files

    def _parse_file(self, fpath: Path, graph: nx.DiGraph) -> None:
        try:
            content = fpath.read_text(errors="replace")
        except Exception as exc:
            logger.warning("Cannot read %s: %s", fpath, exc)
            return

        language = _LANG_MAP.get(fpath.suffix)
        if language == "python":
            self._python.parse(fpath, content, graph)
        elif language in ("haskell", "javascript", "typescript"):
            self._treesitter.parse(fpath, content, graph, language)
        else:
            self._regex.parse(fpath, content, graph)

    def parse_file(self, fpath: Path) -> nx.DiGraph:
        """Parse a single file and return a fresh graph (for testing)."""
        graph = nx.DiGraph()
        self._parse_file(fpath, graph)
        return graph

    @staticmethod
    def graph_to_serializable(graph: nx.DiGraph) -> dict:
        """Convert graph to a JSON-serializable dict for session cache."""
        data = nx.node_link_data(graph)
        return {
            "nodes": {n: graph.nodes[n] for n in graph.nodes},
            "edges": {
                n: {
                    "calls": [v for u, v, d in graph.out_edges(n, data=True) if d.get("rel") == "calls"],
                    "called_by": [u for u, v, d in graph.in_edges(n, data=True) if d.get("rel") == "called_by"],
                    "imports": [v for u, v, d in graph.out_edges(n, data=True) if d.get("rel") == "imports"],
                }
                for n in graph.nodes
            },
            "_nx": data,
        }
