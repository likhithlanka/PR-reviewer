"""
adapters/pdf.py — PDF fetcher and extractor.
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional

import requests
import pdfplumber

logger = logging.getLogger(__name__)


def fetch_pdf(url: str) -> Optional[list[dict]]:
    """Download a PDF and extract text per page."""
    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            for chunk in r.iter_content(chunk_size=8192):
                tf.write(chunk)
            temp_path = Path(tf.name)

        chunks = []
        with pdfplumber.open(temp_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    chunks.append({
                        "page": i + 1,
                        "text": text.strip(),
                        "url": f"{url}#page={i+1}"
                    })

        temp_path.unlink()
        return chunks

    except Exception as exc:
        logger.error("PDF fetch failed for %s: %s", url, exc)
        return None
