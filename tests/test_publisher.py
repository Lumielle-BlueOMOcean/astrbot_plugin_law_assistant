from __future__ import annotations

import pytest

from tests.fakes import RecordingPublisher


@pytest.mark.asyncio
async def test_publisher_boundary_accepts_text_without_platform_coupling() -> None:
    publisher = RecordingPublisher()

    published = await publisher.publish_text("private:123", "hello")

    assert published is True
    assert publisher.calls == [("private:123", "hello")]
