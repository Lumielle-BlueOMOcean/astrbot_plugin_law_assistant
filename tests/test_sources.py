from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from models import CaseItem, LawUpdate, SourceDocument
from sources.cases import CaseDetailExtractor, CaseSourceAdapter
from sources.china_jm import ChinaJMSourceAdapter
from sources.generic import GenericEventSourceAdapter
from sources.html import extract_links, html_to_text
from sources.law_updates import LawUpdateExtractor

FIXTURES = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, pages: dict[str, tuple[str, str]]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def fetch_document(self, url: str, **kwargs):
        self.calls.append(url)
        content, content_type = self.pages[url]
        return SimpleNamespace(
            url=url,
            text=content,
            content_type=content_type,
            warnings=(),
        )


def fixture_text(name: str, encoding: str = "utf-8") -> str:
    return (FIXTURES / name).read_text(encoding=encoding)


def test_html_helpers_extract_links_and_visible_text() -> None:
    html = fixture_text("generic_notice.html")

    links = extract_links(html, "https://law.example.edu/notices/")

    assert links[0].title == "首页"
    assert "法学院通知" in html_to_text(html)


@pytest.mark.asyncio
async def test_china_jm_adapter_fetches_notice_documents() -> None:
    index_url = "https://china-jm.org/gg/"
    detail_url = "https://china-jm.org/article/?id=771"
    http = FakeHttp(
        {
            index_url: (
                '<a href="/article/?id=771">关于举办法律文书写作大赛的通知</a>',
                "text/html",
            ),
            detail_url: (fixture_text("china_jm_notice.html"), "text/html"),
        }
    )
    adapter = ChinaJMSourceAdapter(http)

    documents = await adapter.fetch()

    assert documents[0].source_key == "china_jm"
    assert documents[0].source_item_key == "771"
    assert "法律文书写作" in documents[0].content
    assert http.calls == [index_url, detail_url]


@pytest.mark.asyncio
async def test_generic_adapter_uses_same_list_detail_pipeline() -> None:
    index_url = "https://law.example.edu/notices/"
    detail_url = "https://law.example.edu/notices/1.html"
    http = FakeHttp(
        {
            index_url: (
                '<a href="/notices/1.html">案例征集通知</a>',
                "text/html",
            ),
            detail_url: (fixture_text("generic_notice.html"), "text/html"),
        }
    )
    adapter = GenericEventSourceAdapter("extra:law-school", index_url, http)

    documents = await adapter.fetch()

    assert documents[0].source_key == "extra:law-school"
    assert documents[0].source_item_key == "1.html"


@pytest.mark.asyncio
async def test_case_adapter_and_extractors_preserve_authority_and_source() -> None:
    index_url = "https://court.example/cases"
    detail_url = "https://court.example/cases/1"
    http = FakeHttp(
        {
            index_url: ('<a href="/cases/1">产权典型案例</a>', "text/html"),
            detail_url: (fixture_text("court_case.html"), "text/html"),
        }
    )
    adapter = CaseSourceAdapter("court_cases", "最高人民法院", index_url, http)
    documents = await adapter.fetch()
    item = await CaseDetailExtractor("最高人民法院").extract(documents[0])

    assert isinstance(item, CaseItem)
    assert item.authority == "最高人民法院"
    assert item.source_url == detail_url
    assert "产权" in item.raw_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "authority", "index_url", "index_fixture", "detail_urls", "detail_fixture"),
    [
        (
            "court_cases",
            "最高人民法院",
            "https://www.court.gov.cn/zixun/gengduo/104.html",
            "court_cases_index.html",
            [
                "https://www.court.gov.cn/zixun/xiangqing/123456.html",
                "https://www.court.gov.cn/zixun/xiangqing/123457.html",
            ],
            "court_case.html",
        ),
        (
            "spp_cases",
            "最高人民检察院",
            "https://www.spp.gov.cn/spp/zgjdxal/",
            "spp_cases_index.html",
            [
                "https://www.spp.gov.cn/spp/xwfbh/202609/t20260917_123456.shtml",
                "https://www.spp.gov.cn/spp/xwfbh/202609/t20260916_123457.shtml",
            ],
            "spp_case.html",
        ),
    ],
)
async def test_official_case_adapters_filter_navigation_links(
    key, authority, index_url, index_fixture, detail_urls, detail_fixture
) -> None:
    pages = {index_url: (fixture_text(index_fixture), "text/html")}
    pages.update(
        {url: (fixture_text(detail_fixture), "text/html") for url in detail_urls}
    )
    http = FakeHttp(pages)

    documents = await CaseSourceAdapter(key, authority, index_url, http).fetch()

    assert [document.url for document in documents] == detail_urls
    assert http.calls == [index_url, *detail_urls]


def test_law_update_extractor_returns_structured_update() -> None:
    document = SourceDocument(
        source_key="npc_laws",
        source_item_key="1",
        url="https://flk.npc.gov.cn/law/1.html",
        title="中华人民共和国网络安全法实施条例",
        content=fixture_text("law_update.html"),
        fetched_at="2026-09-17T00:00:00+08:00",
    )

    update = LawUpdateExtractor().extract(document)

    assert isinstance(update, LawUpdate)
    assert update.title == "中华人民共和国网络安全法实施条例"
    assert update.source_url == document.url
