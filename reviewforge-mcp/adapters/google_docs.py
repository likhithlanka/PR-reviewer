"""
adapters/google_docs.py — Google Docs fetcher (if configured).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_google_doc(url: str) -> Optional[list[dict]]:
    """
    Export Google Doc as plain text. (Placeholder for full OAuth flow).
    For now, attempts the public export endpoint if accessible.
    """
    import re
    import requests
    
    match = re.search(r"/document/d/([^/]+)/", url)
    if not match:
        logger.warning("Could not extract Google Doc ID from URL: %s", url)
        return None
    
    doc_id = match.group(1)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

    try:
        r = requests.get(export_url, timeout=15)
        if r.status_code == 200:
            text = r.text
            # Basic chunking by double newline
            raw_chunks = text.split("\n\n")
            chunks = []
            current_chunk = []
            for line in raw_chunks:
                current_chunk.append(line)
                if sum(len(c) for c in current_chunk) > 1000:
                    chunks.append({
                        "text": "\n\n".join(current_chunk),
                        "url": url
                    })
                    current_chunk = []
            if current_chunk:
                chunks.append({"text": "\n\n".join(current_chunk), "url": url})
            return chunks
        else:
            logger.warning("Google doc export failed (likely requires auth): %s", r.status_code)
            return None
    except Exception as exc:
        logger.error("Failed to fetch Google Doc: %s", exc)
        return None
