"""
adapters/confluence.py — Confluence REST API fetcher.
"""

import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)


def fetch_confluence(url: str) -> Optional[list[dict]]:
    """Fetch Confluence page by URL and extract structured text."""
    if not config.CONFLUENCE_USERNAME or not config.CONFLUENCE_API_TOKEN:
        logger.warning("Confluence credentials not configured.")
        return None

    # Naive URL parse: assumes https://{domain}/wiki/spaces/{space}/pages/{page_id}/{title}
    # Or just extract the page ID if it's there.
    import re
    match = re.search(r"/pages/(\d+)", url)
    if not match:
        logger.warning("Could not extract Confluence page ID from URL: %s", url)
        return None
    
    page_id = match.group(1)
    base_domain = url.split("/wiki/")[0]
    api_url = f"{base_domain}/wiki/rest/api/content/{page_id}?expand=body.storage,version"

    try:
        r = requests.get(
            api_url,
            auth=(config.CONFLUENCE_USERNAME, config.CONFLUENCE_API_TOKEN),
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        html = data.get("body", {}).get("storage", {}).get("value", "")
        if not html:
            return []

        # Convert to plain text with minimal formatting
        soup = BeautifulSoup(html, "lxml")
        
        # Super naive chunking: chunk by <h1>, <h2>, <h3>
        chunks: list[dict] = []
        current_section = "Document Start"
        current_text = []

        for element in soup.body.children if soup.body else soup.children:
            if getattr(element, 'name', None) in ("h1", "h2", "h3"):
                if current_text:
                    chunks.append({
                        "section": current_section,
                        "text": "\n".join(current_text).strip(),
                        "url": url,
                    })
                current_section = element.get_text(separator=' ', strip=True) # type: ignore
                current_text = []
            elif hasattr(element, "get_text"):
                text = element.get_text(separator=' ', strip=True) # type: ignore
                if text:
                    current_text.append(text)

        if current_text:
            chunks.append({
                "section": current_section,
                "text": "\n".join(current_text).strip(),
                "url": url,
            })

        return chunks

    except Exception as exc:
        logger.error("Failed to fetch Confluence page %s: %s", url, exc)
        return None
