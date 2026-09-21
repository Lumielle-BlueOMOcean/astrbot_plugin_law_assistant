from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

if __package__ and "." in __package__:
    from ..models import SourceDocument
    from .generic import GenericEventSourceAdapter
    from .html import (
        extract_links,
        fetch_relevant_pdf_attachments,
        html_title,
        same_host,
    )
else:
    from models import SourceDocument
    from sources.generic import GenericEventSourceAdapter
    from sources.html import (
        extract_links,
        fetch_relevant_pdf_attachments,
        html_title,
        same_host,
    )


CHINA_JM_URL = "https://china-jm.org/gg/"


class ChinaJMSourceAdapter(GenericEventSourceAdapter):
    """Adapter for the China legal master competition notice list."""

    key = "china_jm"

    def __init__(
        self, http: Any, index_url: str = CHINA_JM_URL, *, max_items: int = 50
    ):
        super().__init__(self.key, index_url, http, max_items=max_items)

    async def fetch(self) -> list[SourceDocument]:
        index = await self.http.fetch_document(self.index_url)
        documents: list[SourceDocument] = []
        seen: set[str] = set()
        for link in extract_links(index.text, index.url):
            parsed = urlparse(link.url)
            if not same_host(link.url, self.index_url):
                continue
            if parsed.path.rstrip("/") != "/article":
                continue
            item_key = parse_qs(parsed.query).get("id", [""])[0].strip()
            if not item_key or link.url in seen:
                continue
            seen.add(link.url)
            if len(documents) >= self.max_items:
                break
            detail = await self.http.fetch_document(link.url)
            if not self._is_allowed_detail_url(detail.url):
                continue
            content, attachments, warnings = await fetch_relevant_pdf_attachments(
                self.http,
                detail,
                allowed_hosts={urlparse(self.index_url).netloc.lower()},
            )
            documents.append(
                SourceDocument(
                    source_key=self.key,
                    source_item_key=item_key,
                    url=detail.url,
                    title=html_title(content, link.title),
                    content=content,
                    fetched_at=getattr(index, "fetched_at", "") or _now_iso(),
                    content_type=getattr(detail, "content_type", "text/html"),
                    metadata={
                        "index_url": self.index_url,
                        "link_title": link.title,
                        "attachment_warnings": list(getattr(detail, "warnings", ()))
                        + list(warnings),
                    },
                    attachments=attachments,
                )
            )
        return documents


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["CHINA_JM_URL", "ChinaJMSourceAdapter"]
