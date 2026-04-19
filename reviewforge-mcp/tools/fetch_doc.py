"""
tools/fetch_doc.py — Document fetcher and vectorizer MCP tool.
MCP tool: fetch_doc
"""

import logging
from urllib.parse import urlparse
from typing import Optional

from mcp.types import Tool

import config

logger = logging.getLogger(__name__)


class FetchDocTool:
    def definition(self) -> Tool:
        return Tool(
            name="fetch_doc",
            description=(
                "Download and extract text from a given URL (Confluence, Google Docs, PDF, or general web). "
                "The document is chunked and vectorized into the repository's 'docs' collection "
                "so it can be semantically searched via the search tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to the document or spec to fetch.",
                    },
                    "repo_slug": {
                        "type": "string",
                        "description": "Target repo slug to store the vector index.",
                    },
                    "doc_id": {
                        "type": "string",
                        "description": "Unique identifier/name for this document in the index.",
                    },
                },
                "required": ["url", "repo_slug", "doc_id"],
            },
        )

    async def run(self, args: dict) -> str:
        from pipeline.vectorizer import Vectorizer
        import adapters.confluence as conf
        import adapters.google_docs as gdocs
        import adapters.pdf as pdf

        url: str = args["url"]
        repo_slug: str = args["repo_slug"]
        doc_id: str = args["doc_id"]

        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        chunks: Optional[list[dict]] = None

        if "confluence" in domain or url.startswith(config.CONFLUENCE_URL):
            chunks = conf.fetch_confluence(url)
            source_type = "Confluence"
        elif "docs.google.com" in domain:
            chunks = gdocs.fetch_google_doc(url)
            source_type = "Google Docs"
        elif url.endswith(".pdf"):
            chunks = pdf.fetch_pdf(url)
            source_type = "PDF"
        else:
            # Fallback for generic web pages
            try:
                import requests
                from bs4 import BeautifulSoup
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                text = soup.get_text(separator="\n", strip=True)
                # Chunk every 100 lines roughly
                lines = text.splitlines()
                chunks = []
                for i in range(0, len(lines), 100):
                    chunks.append({"text": "\n".join(lines[i:i+100]), "url": url})
                source_type = "Web Page"
            except Exception as exc:
                return f"Failed to fetch {url}: {exc}"

        if not chunks:
            return f"Failed to extract any text from {url} ({source_type})."

        try:
            vectorizer = Vectorizer(repo_slug)
            indexed_count = vectorizer.index_document(doc_id, chunks)
            return (
                f"Successfully fetched {source_type} document.\n"
                f"Extracted {len(chunks)} sections/pages.\n"
                f"Indexed {indexed_count} chunks into the '{repo_slug}' docs vector collection.\n"
                f"You can now query this document using the `search` tool."
            )
        except Exception as exc:
            return f"Failed to vectorize document: {exc}"
