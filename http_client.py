from __future__ import annotations

import asyncio
import io
import re
from dataclasses import dataclass
from email.message import Message
from typing import Any, Self

import aiohttp


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    text: str
    content_type: str
    status: int
    warnings: tuple[str, ...] = ()


class AsyncHttpClient:
    """Small shared async HTTP client with bounded timeout and two attempts."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        user_agent: str = "Lumielle-Law-Assistant/0.2.0",
        session: aiohttp.ClientSession | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self._session = session
        self._owns_session = session is None
        self._sleep = sleep

    async def __aenter__(self) -> Self:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                headers={"User-Agent": self.user_agent},
                trust_env=True,
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_document(self, url: str, **kwargs: Any) -> FetchResult:
        if self._session is None:
            await self.__aenter__()
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with self._session.get(
                    url,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                    headers={"User-Agent": self.user_agent},
                    **kwargs,
                ) as response:
                    body = await response.read()
                    if response.status >= 400:
                        raise RuntimeError(f"HTTP {response.status} for {url}")
                    content_type = response.headers.get("Content-Type", "")
                    if "pdf" in content_type.lower() or url.lower().split("?")[
                        0
                    ].endswith(".pdf"):
                        text, warnings = _extract_pdf_text(body)
                    else:
                        text, warnings = _decode_html(body, content_type)
                    return FetchResult(
                        url=str(response.url),
                        text=text,
                        content_type=content_type or "text/html",
                        status=response.status,
                        warnings=warnings,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
                last_error = exc
                if attempt == 0:
                    await self._sleep(0.5)
        raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def _decode_html(body: bytes, content_type: str) -> tuple[str, tuple[str, ...]]:
    encoding = _charset_from_content_type(content_type) or _charset_from_meta(body)
    candidates = [encoding] if encoding else []
    candidates.extend(["utf-8", "gb18030", "big5", "latin-1"])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return body.decode(candidate), ()
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace"), ("encoding_replaced",)


def _charset_from_content_type(content_type: str) -> str | None:
    message = Message()
    message["content-type"] = content_type
    return message.get_param("charset", header="content-type")


def _charset_from_meta(body: bytes) -> str | None:
    sample = body[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", sample, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_pdf_text(body: bytes) -> tuple[str, tuple[str, ...]]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        return text, () if text else ("pdf_text_empty",)
    except Exception:  # noqa: BLE001 - malformed PDFs must fail soft per source
        return "", ("pdf_text_extraction_failed",)
