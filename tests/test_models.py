from __future__ import annotations

from models import SourceDocument


def test_source_document_hash_is_deterministic() -> None:
    first = SourceDocument(
        source_key="fake",
        source_item_key="item-1",
        url="https://example.test/item-1",
        title="Example",
        content="same content",
        fetched_at="2026-09-17T00:00:00+00:00",
    )
    second = SourceDocument(
        source_key="fake",
        source_item_key="item-1",
        url="https://example.test/item-1",
        title="Example",
        content="same content",
        fetched_at="2026-09-17T00:00:00+00:00",
    )

    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64
