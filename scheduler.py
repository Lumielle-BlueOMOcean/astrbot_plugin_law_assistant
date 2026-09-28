from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any


class LawAssistantScheduler:
    """Own independent interval-scan and due-time task lifecycles."""

    def __init__(
        self,
        service: Any,
        *,
        enabled: bool,
        interval_minutes: int,
        scan_enabled: bool | None = None,
        due_enabled: bool | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        logger: Any | None = None,
    ) -> None:
        self.service = service
        self.enabled = bool(enabled)
        self.scan_enabled = (
            bool(enabled) if scan_enabled is None else bool(scan_enabled)
        )
        self.due_enabled = bool(enabled) if due_enabled is None else bool(due_enabled)
        self.interval_minutes = max(1, int(interval_minutes))
        self.sleep = sleep
        self.logger = logger or logging.getLogger(__name__)
        self._scan_task: asyncio.Task[None] | None = None
        self._due_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()

    @property
    def task(self) -> asyncio.Task[None] | None:
        """Compatibility alias for callers that previously observed one task."""
        return self._scan_task or self._due_task

    @property
    def scan_task(self) -> asyncio.Task[None] | None:
        return self._scan_task

    @property
    def due_task(self) -> asyncio.Task[None] | None:
        return self._due_task

    async def start(self) -> None:
        if not self.enabled:
            return
        if self.scan_enabled and (self._scan_task is None or self._scan_task.done()):
            self._scan_task = asyncio.create_task(
                self._run_scans(), name="law-assistant-scan-scheduler"
            )
        await self._ensure_due_task()

    async def wake(self) -> None:
        """Start due work on demand after a confirmed plan or reveal is saved."""
        self.enabled = True
        self.due_enabled = True
        self._wake_event.set()
        await self._ensure_due_task()

    async def _ensure_due_task(self) -> None:
        has_work = getattr(self.service, "has_due_scheduler_work", None)
        if callable(has_work) and not has_work():
            self.due_enabled = False
            return
        if (
            self.enabled
            and self.due_enabled
            and hasattr(self.service, "process_due_work")
            and (self._due_task is None or self._due_task.done())
        ):
            self._due_task = asyncio.create_task(
                self._run_due_work(), name="law-assistant-due-scheduler"
            )

    async def stop(self) -> None:
        tasks = [task for task in (self._scan_task, self._due_task) if task is not None]
        self._scan_task = None
        self._due_task = None
        self.enabled = False
        self._wake_event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_scans(self) -> None:
        while True:
            await self.sleep(self.interval_minutes * 60)
            try:
                if hasattr(self.service, "run_scheduled_scan_jobs"):
                    await self.service.run_scheduled_scan_jobs()
                elif hasattr(self.service, "run_scheduled_jobs"):
                    await self.service.run_scheduled_jobs()
                else:
                    await self.service.scan_events(trigger="scheduler")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Law Assistant scheduled scan failed")

    async def _run_due_work(self) -> None:
        while True:
            self._wake_event.clear()
            try:
                await self.service.process_due_work()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Law Assistant due-work reconciliation failed")

            has_work = getattr(self.service, "has_due_scheduler_work", None)
            if callable(has_work) and not has_work():
                self.due_enabled = False
                self._due_task = None
                return

            delay_seconds = 24 * 60 * 60
            try:
                due_at = self.service.next_due_at(datetime.now(timezone.utc))
                if due_at is not None:
                    if due_at.tzinfo is None:
                        raise ValueError(
                            "next_due_at must return a timezone-aware datetime"
                        )
                    delay_seconds = max(
                        0.0,
                        (
                            due_at.astimezone(timezone.utc) - datetime.now(timezone.utc)
                        ).total_seconds(),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Unable to resolve next Law Assistant due time")

            await self._wait_for_wake_or_timeout(delay_seconds)

    async def _wait_for_wake_or_timeout(self, seconds: float) -> None:
        sleeper = asyncio.create_task(self.sleep(max(0.0, seconds)))
        wake = asyncio.create_task(self._wake_event.wait())
        try:
            await asyncio.wait({sleeper, wake}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper, wake):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper, wake, return_exceptions=True)


__all__ = ["LawAssistantScheduler"]
