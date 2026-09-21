from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

if __package__ and "." in __package__:
    from ..models import SourceDocument
    from .html import (
        extract_links,
        fetch_relevant_pdf_attachments,
        html_title,
        item_key_from_url,
        same_host,
    )
else:
    from models import SourceDocument
    from sources.html import (
        extract_links,
        fetch_relevant_pdf_attachments,
        html_title,
        item_key_from_url,
        same_host,
    )


class GenericEventSourceAdapter:
    """Fetch a conventional list page followed by same-host detail pages."""

    def __init__(self, key: str, index_url: str, http: Any, *, max_items: int = 50):
        self.key = key
        self.index_url = index_url
        self.http = http
        self.max_items = max(1, max_items)

    async def fetch(self) -> list[SourceDocument]:
        index = await self.http.fetch_document(self.index_url)
        documents: list[SourceDocument] = []
        seen: set[str] = set()
        for link in extract_links(index.text, index.url):
            if not same_host(link.url, self.index_url) or link.url in seen:
                continue
            if link.url.rstrip("/") == self.index_url.rstrip("/"):
                continue
            if not self._is_candidate_link(link):
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
                    source_item_key=_generic_item_key(link.url),
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

    def _is_candidate_link(self, link: Any) -> bool:
        """Allow a specialized source to exclude navigation links."""
        return True

    def _is_allowed_detail_url(self, url: str) -> bool:
        parsed = urlparse(url)
        base = urlparse(self.index_url)
        return parsed.scheme in {"http", "https"} and (
            parsed.netloc.lower() == base.netloc.lower()
        )


def _generic_item_key(url: str) -> str:
    return item_key_from_url(url).lstrip("/")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["GenericEventSourceAdapter"]
