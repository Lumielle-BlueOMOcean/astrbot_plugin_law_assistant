from __future__ import annotations

import logging
from typing import Protocol


class Publisher(Protocol):
    async def publish_text(self, destination: str, text: str) -> bool:
        """Publish ordinary text through a host-provided transport."""


class NoopPublisher:
    """Phase 1 publisher boundary; it intentionally performs no external send."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def publish_text(self, destination: str, text: str) -> bool:
        self.logger.info("Publisher is not enabled in Phase 1: %s", destination)
        return False
