from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any


class LawAssistantScheduler:
    def __init__(
        self,
        service: Any,
        *,
        enabled: bool,
        interval_minutes: int,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        logger: Any | None = None,
    ) -> None:
        self.service = service
        self.enabled = enabled
        self.interval_minutes = interval_minutes
        self.sleep = sleep
        self.logger = logger or logging.getLogger(__name__)
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name="law-assistant-scheduler"
            )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            await self.sleep(self.interval_minutes * 60)
            try:
                await self.service.scan_events(trigger="scheduler")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Law Assistant scheduler scan failed")
