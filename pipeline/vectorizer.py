"""
pipeline/vectorizer.py — AST-aware chunking and ChromaDB vector indexing.

Two collections per repo: `code` (codebase) and `docs` (linked specs/documents).
Chunks are at AST boundaries (per function/class), not arbitrary token windows.
Incremental: only re-embeds changed files.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)


def _get_embedding_function():
    """Return ChromaDB embedding function based on config."""
    if config.EMBEDDING_PROVIDER == "openai" and config.OPENAI_API_KEY:
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
        return OpenAIEmbeddingFunction(
            api_key=config.OPENAI_API_KEY,
            model_name="text-embedding-3-small",
        )
    else:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        return SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )


class Vectorizer:
    """Handles chunking and ChromaDB indexing for a single repository."""

    def __init__(self, repo_slug: str) -> None:
        self.repo_slug = repo_slug
        self._client = self._make_client()
        self._ef = _get_embedding_function()
        self._code_col = self._client.get_or_create_collection(
            name="code", embedding_function=self._ef
        )
        self._docs_col = self._client.get_or_create_collection(
            name="docs", embedding_function=self._ef
        )

    def _make_client(self):
        import chromadb
        vector_dir = config.VECTORS_DIR / self.repo_slug
        vector_dir.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=str(vector_dir))

    # ── Code indexing ──────────────────────────────────────────────────────

    def index_repo(self, repo_path: Path, graph, changed_files: Optional[list[str]] = None) -> int:
        """
        Index codebase into the `code` collection.
        If changed_files provided, only re-index those files.
        Returns number of chunks indexed.
        """
        import networkx as nx
        chunks_indexed = 0

        # Build AST-based chunks from graph nodes
        for node_id, data in graph.nodes(data=True):
            if data.get("kind") not in ("function", "class"):
                continue
            file_path_str = data.get("file_path", "")
            if not file_path_str:
                continue

            fpath = Path(file_path_str)
            if changed_files and not any(
                file_path_str.endswith(cf) or cf in file_path_str
                for cf in changed_files
            ):
                continue  # Skip unchanged files in incremental mode

            if not fpath.exists():
                continue

            try:
                content = fpath.read_text(errors="replace")
                line_start = data.get("line_start", 1)
                line_end = data.get("line_end", line_start + 50)
                lines = content.splitlines()
                chunk_lines = lines[max(0, line_start - 1):line_end]
                chunk_text = "\n".join(chunk_lines)
                if not chunk_text.strip():
                    continue
            except Exception as exc:
                logger.warning("Could not read %s: %s", fpath, exc)
                continue

            # Remove stale embedding for this node (in case of re-index)
            try:
                self._code_col.delete(ids=[node_id])
            except Exception:
                pass

            callers = [
                u for u, v, d in graph.in_edges(node_id, data=True)
                if d.get("rel") == "called_by"
            ]
            callees = [
                v for u, v, d in graph.out_edges(node_id, data=True)
                if d.get("rel") == "calls"
            ]

            self._code_col.add(
                ids=[node_id],
                documents=[chunk_text],
                metadatas=[{
                    "file_path": file_path_str,
                    "kind": data.get("kind", ""),
                    "name": data.get("name", ""),
                    "line_start": line_start,
                    "line_end": line_end,
                    "language": data.get("language", ""),
                    "callers": ",".join(callers[:10]),
                    "callees": ",".join(callees[:10]),
                }],
            )
            chunks_indexed += 1

        logger.info("Indexed %d code chunks for %s", chunks_indexed, self.repo_slug)
        return chunks_indexed

    def index_file(self, file_path: Path, graph_nodes: list[dict]) -> int:
        """Index a single file's chunks (for testing)."""
        chunks = 0
        content = file_path.read_text(errors="replace")
        lines = content.splitlines()
        for node in graph_nodes:
            chunk = "\n".join(lines[node.get("line_start", 1) - 1: node.get("line_end", 50)])
            if chunk.strip():
                self._code_col.add(
                    ids=[node["id"]],
                    documents=[chunk],
                    metadatas=[{"file_path": str(file_path)}],
                )
                chunks += 1
        return chunks

    # ── Document indexing ──────────────────────────────────────────────────

    def index_document(self, doc_id: str, chunks: list[dict]) -> int:
        """
        Index a fetched document (spec/confluence/etc) into the `docs` collection.
        Each chunk: {text, section, url, page}
        """
        ids, documents, metadatas = [], [], []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}::chunk{i}"
            ids.append(chunk_id)
            documents.append(chunk["text"])
            metadatas.append({
                "doc_id": doc_id,
                "section": chunk.get("section", ""),
                "url": chunk.get("url", ""),
                "page": str(chunk.get("page", "")),
            })
        if ids:
            # Remove stale entries
            try:
                existing = self._docs_col.get(where={"doc_id": doc_id})
                if existing["ids"]:
                    self._docs_col.delete(ids=existing["ids"])
            except Exception:
                pass
            self._docs_col.add(ids=ids, documents=documents, metadatas=metadatas)
        logger.info("Indexed %d doc chunks for %s", len(ids), doc_id)
        return len(ids)

    # ── Search ─────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        n_results: int = 10,
        collection: str = "both",
    ) -> list[dict]:
        """
        Semantic search over code and/or docs collections.
        Returns combined ranked results.
        """
        results = []

        def _query_col(col, label: str) -> None:
            try:
                r = col.query(query_texts=[query], n_results=n_results)
                docs = r.get("documents", [[]])[0]
                metas = r.get("metadatas", [[]])[0]
                dists = r.get("distances", [[]])[0]
                for doc, meta, dist in zip(docs, metas, dists):
                    results.append({
                        "collection": label,
                        "score": round(1 - dist, 4) if dist is not None else None,
                        "content": doc,
                        "metadata": meta,
                    })
            except Exception as exc:
                logger.warning("Search error in %s: %s", label, exc)

        if collection in ("code", "both"):
            _query_col(self._code_col, "code")
        if collection in ("docs", "both"):
            _query_col(self._docs_col, "docs")

        # Sort combined by score descending
        results.sort(key=lambda x: x.get("score") or 0, reverse=True)
        return results[:n_results]

    def grep_search(self, query: str, repo_path: Path) -> list[dict]:
        """Exact ripgrep search as fallback."""
        try:
            out = subprocess.run(
                ["rg", "--json", "-i", query, str(repo_path)],
                capture_output=True, text=True, timeout=15,
            )
            import json
            results = []
            for line in out.stdout.splitlines():
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "match":
                        data = obj["data"]
                        results.append({
                            "collection": "grep",
                            "file": data["path"]["text"],
                            "line": data["line_number"],
                            "content": data["lines"]["text"].strip(),
                        })
                except Exception:
                    pass
            return results[:20]
        except FileNotFoundError:
            # rg not installed — use grep
            try:
                out = subprocess.run(
                    ["grep", "-rn", "-i", query, str(repo_path)],
                    capture_output=True, text=True, timeout=15,
                )
                results = []
                for line in out.stdout.splitlines()[:20]:
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        results.append({
                            "collection": "grep",
                            "file": parts[0],
                            "line": parts[1],
                            "content": parts[2].strip(),
                        })
                return results
            except Exception:
                return []
        except Exception as exc:
            logger.warning("grep_search error: %s", exc)
            return []
