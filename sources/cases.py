from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

if __package__ and "." in __package__:
    from ..date_parser import extract_publication_datetime
    from ..models import CaseItem, SourceDocument
    from .generic import GenericEventSourceAdapter
    from .html import html_title, html_to_text
else:
    from date_parser import extract_publication_datetime
    from models import CaseItem, SourceDocument
    from sources.generic import GenericEventSourceAdapter
    from sources.html import html_title, html_to_text


class CaseSourceAdapter(GenericEventSourceAdapter):
    def __init__(
        self,
        key: str,
        authority: str,
        index_url: str,
        http: Any,
        *,
        max_items: int = 20,
    ):
        super().__init__(key, index_url, http, max_items=max_items)
        self.authority = authority

    def _is_candidate_link(self, link: Any) -> bool:
        """Keep official list navigation out of the case detail fetch set."""
        host = urlparse(link.url).netloc.lower()
        if host == "www.court.gov.cn":
            return bool(
                re.fullmatch(r"/zixun/xiangqing/\d+\.html", urlparse(link.url).path)
            )
        if host == "www.spp.gov.cn":
            path = urlparse(link.url).path
            if re.fullmatch(r"/spp/zgjdxal(?:/index(?:_\d+)?\.s?html)?/?", path):
                return False
            return any(marker in link.title for marker in ("典型案例", "典型事例"))
        return True


class CaseDetailExtractor:
    def __init__(self, authority: str, *, timezone_name: str = "Asia/Shanghai"):
        self.authority = authority
        self.timezone_name = timezone_name

    async def extract(self, document: SourceDocument) -> CaseItem:
        raw_text = html_to_text(document.content)
        title = html_title(document.content, document.title)
        published_at = extract_publication_datetime(
            raw_text, timezone_name=self.timezone_name
        )
        return CaseItem(
            source_key=document.source_key,
            source_item_key=document.source_item_key,
            title=title or document.title,
            source_url=document.url,
            authority=self.authority,
            raw_text=raw_text or document.content,
            content_hash=document.content_hash
            or hashlib.sha256(document.content.encode()).hexdigest(),
            published_at=published_at,
            discovered_at=document.fetched_at,
            last_seen_at=document.fetched_at,
            metadata={"content_type": document.content_type},
        )


def extract_case_number(text: str) -> str:
    match = re.search(r"[（(]?\d{4}[）)]?[^\n]{0,20}?号", text)
    return match.group(0).strip() if match else ""


__all__ = ["CaseDetailExtractor", "CaseSourceAdapter", "extract_case_number"]
