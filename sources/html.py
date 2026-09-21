from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


@dataclass(frozen=True, slots=True)
class Link:
    title: str
    url: str


def extract_links(html: str, base_url: str) -> list[Link]:
    soup = BeautifulSoup(html, "html.parser")
    result: list[Link] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        title = title or str(anchor.get("title", "")).strip()
        if not title:
            continue
        seen.add(url)
        result.append(Link(title=title, url=url))
    return result


def html_title(html: str, fallback: str = "") -> str:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(["h1", "h2"])
    title = heading.get_text(" ", strip=True) if heading else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    return " ".join(title.split()) or fallback


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "nav", "footer"]):
        tag.decompose()
    candidates = [
        node
        for selector in (
            "article",
            "main",
            ".article-content",
            ".article",
            ".content",
            ".wp",
        )
        for node in soup.select(selector)
    ]
    node = max(
        candidates,
        key=lambda item: len(item.get_text(" ", strip=True)),
        default=soup.body,
    )
    if node is None:
        return ""
    visible = "\n".join(
        line.strip()
        for line in node.get_text("\n", strip=True).splitlines()
        if line.strip()
    )
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if page_title and page_title not in visible:
        return f"{page_title}\n{visible}" if visible else page_title
    return visible


def same_host(url: str, base_url: str) -> bool:
    return urlparse(url).netloc == urlparse(base_url).netloc


def item_key_from_url(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.path.rstrip("/") or "/") + (
        f"?{parsed.query}" if parsed.query else ""
    )


async def fetch_relevant_pdf_attachments(
    http: Any,
    detail: Any,
    *,
    allowed_hosts: set[str] | None = None,
    max_bytes: int = 8 * 1024 * 1024,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Fetch linked PDFs only when the HTML body points to an attachment."""
    if "pdf" in str(getattr(detail, "content_type", "")).lower():
        return getattr(detail, "text", ""), (), ()
    original = getattr(detail, "text", "")
    combined = original
    visible = html_to_text(original)
    if len(visible) >= 120 and not any(
        marker in visible for marker in ("详见附件", "附件下载", "附件：")
    ):
        return original, (), ()
    attachments: list[str] = []
    warnings: list[str] = []
    for link in extract_links(getattr(detail, "text", ""), getattr(detail, "url", "")):
        if not link.url.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        parsed = urlparse(link.url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if allowed_hosts and parsed.netloc.lower() not in allowed_hosts:
            warnings.append("attachment_host_rejected")
            continue
        try:
            pdf = await http.fetch_document(link.url, max_bytes=max_bytes)
            final_host = urlparse(str(getattr(pdf, "url", link.url))).netloc.lower()
            if allowed_hosts and final_host not in allowed_hosts:
                warnings.append("attachment_redirect_rejected")
                continue
            attachments.append(link.url)
            if getattr(pdf, "text", ""):
                combined = f"{combined}\n\n[附件原文 {link.url}]\n{pdf.text}"
        except Exception as exc:  # noqa: BLE001 - optional attachment is fail-soft
            # The caller still preserves the URL; source-level failure is not caused by
            # one optional attachment.
            warnings.append(f"attachment_fetch_failed:{type(exc).__name__}")
    return combined, tuple(attachments), tuple(warnings)
