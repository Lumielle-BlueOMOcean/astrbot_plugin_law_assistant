"""Optional read-only smoke for the configured public source pages."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ and "." in __package__:
    from ..http_client import AsyncHttpClient
    from ..sources.cases import CaseSourceAdapter
    from ..sources.china_jm import ChinaJMSourceAdapter
    from ..sources.html import html_to_text
else:
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from http_client import AsyncHttpClient
    from sources.cases import CaseSourceAdapter
    from sources.china_jm import ChinaJMSourceAdapter
    from sources.html import html_to_text


async def _probe(name: str, adapter) -> None:
    try:
        documents = await adapter.fetch()
        body_length = sum(len(html_to_text(document.content)) for document in documents)
        print(f"{name}: PASS documents={len(documents)} text_chars={body_length}")
    except Exception as exc:  # noqa: BLE001 - smoke records source failure and continues
        print(f"{name}: FAIL {exc}")


async def main() -> None:
    async with AsyncHttpClient(timeout_seconds=20) as http:
        await _probe("china_jm", ChinaJMSourceAdapter(http, max_items=3))
        await _probe(
            "court",
            CaseSourceAdapter(
                "court_cases",
                "最高人民法院",
                "https://www.court.gov.cn/zixun/gengduo/104.html",
                http,
                max_items=3,
            ),
        )
        await _probe(
            "spp",
            CaseSourceAdapter(
                "spp_cases",
                "最高人民检察院",
                "https://www.spp.gov.cn/spp/zgjdxal/",
                http,
                max_items=3,
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
