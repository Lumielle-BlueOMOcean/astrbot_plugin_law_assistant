from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

OVERDUE_RECHECK_SECONDS = 5.0
DUE_RESOLUTION_RETRY_SECONDS = 30.0


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
        clock: Callable[[], datetime] | None = None,
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
        service_clock = getattr(service, "clock", None)
        self.clock = clock or (
            service_clock
            if callable(service_clock)
            else lambda: datetime.now(timezone.utc)
        )
        self.logger = logger or logging.getLogger(__name__)
        self._scheduler_id = uuid4().hex[:12]
        self._scan_task: asyncio.Task[None] | None = None
        self._due_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self.logger.info(
            "scheduler_event=created scheduler_id=%s enabled=%s scan_enabled=%s due_enabled=%s",
            self._scheduler_id,
            self.enabled,
            self.scan_enabled,
            self.due_enabled,
        )

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
            self.logger.info(
                "scheduler_event=start_skipped scheduler_id=%s stop_reason=disabled",
                self._scheduler_id,
            )
            return
        self.logger.info("scheduler_event=started scheduler_id=%s", self._scheduler_id)
        if self.scan_enabled and (self._scan_task is None or self._scan_task.done()):
            self._scan_task = asyncio.create_task(
                self._run_scans(), name="law-assistant-scan-scheduler"
            )
        await self._ensure_due_task()

    async def wake(self) -> None:
        """Start due work on demand after a confirmed plan or reveal is saved."""
        self.logger.info(
            "scheduler_event=manual_wake scheduler_id=%s", self._scheduler_id
        )
        self.enabled = True
        self.due_enabled = True
        self._wake_event.set()
        await self._ensure_due_task()

    async def _ensure_due_task(self) -> None:
        has_work = getattr(self.service, "has_due_scheduler_work", None)
        if callable(has_work) and not has_work():
            self.due_enabled = False
            self.logger.info(
                "scheduler_event=due_loop_not_created scheduler_id=%s stop_reason=no_due_work",
                self._scheduler_id,
            )
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
            self.logger.info(
                "scheduler_event=due_loop_created scheduler_id=%s", self._scheduler_id
            )

    async def stop(self) -> None:
        tasks = [task for task in (self._scan_task, self._due_task) if task is not None]
        self._scan_task = None
        self._due_task = None
        self.enabled = False
        self._wake_event.set()
        self.logger.info(
            "scheduler_event=stopping scheduler_id=%s task_count=%s stop_reason=lifecycle_terminate",
            self._scheduler_id,
            len(tasks),
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.logger.info("scheduler_event=stopped scheduler_id=%s", self._scheduler_id)

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
            except Exception as exc:  # noqa: BLE001 - scheduler scan is fail-soft
                self.logger.warning(
                    "scheduler_event=scan_failed scheduler_id=%s error_type=%s",
                    self._scheduler_id,
                    type(exc).__name__,
                )

    async def _run_due_work(self) -> None:
        wake_reason = "startup_catch_up"
        while True:
            self._wake_event.clear()
            task_id = uuid4().hex[:12]
            current = self.clock()
            wake_due_at = None
            try:
                wake_due_at = self.service.next_due_at(current)
                if wake_due_at is not None and wake_due_at.tzinfo is None:
                    raise ValueError(
                        "next_due_at must return a timezone-aware datetime"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - due work still gets its catch-up pass
                self.logger.warning(
                    "scheduler_event=wake_plan_failed scheduler_id=%s task_id=%s error_type=%s",
                    self._scheduler_id,
                    task_id,
                    type(exc).__name__,
                )
                wake_due_at = None
            wake_delay_seconds = (
                (
                    current.astimezone(timezone.utc)
                    - wake_due_at.astimezone(timezone.utc)
                ).total_seconds()
                if wake_due_at is not None
                else None
            )
            self.logger.info(
                "scheduler_event=wake scheduler_id=%s task_id=%s wake_reason=%s wake_at=%s planned_due_at=%s wake_delay_seconds=%s",
                self._scheduler_id,
                task_id,
                wake_reason,
                current.isoformat(),
                wake_due_at.isoformat() if wake_due_at is not None else "none",
                f"{wake_delay_seconds:.3f}"
                if wake_delay_seconds is not None
                else "none",
            )
            try:
                await self.service.process_due_work(now=current, task_id=task_id)
            except asyncio.CancelledError:
                self.logger.info(
                    "scheduler_event=due_work_stopped scheduler_id=%s task_id=%s stop_reason=cancelled",
                    self._scheduler_id,
                    task_id,
                )
                raise
            except Exception as exc:  # noqa: BLE001 - due cycle is fail-soft
                self.logger.warning(
                    "scheduler_event=due_work_failed scheduler_id=%s task_id=%s error_type=%s",
                    self._scheduler_id,
                    task_id,
                    type(exc).__name__,
                )

            has_work = getattr(self.service, "has_due_scheduler_work", None)
            if callable(has_work) and not has_work():
                self.due_enabled = False
                self._due_task = None
                self.logger.info(
                    "scheduler_event=due_loop_stopped scheduler_id=%s stop_reason=no_due_work",
                    self._scheduler_id,
                )
                return

            delay_seconds = 24 * 60 * 60
            due_at = None
            planned_at = self.clock()
            try:
                due_at = self.service.next_due_at(planned_at)
                if due_at is not None:
                    if due_at.tzinfo is None:
                        raise ValueError(
                            "next_due_at must return a timezone-aware datetime"
                        )
                    delay_seconds = max(
                        0.0,
                        (
                            due_at.astimezone(timezone.utc)
                            - planned_at.astimezone(timezone.utc)
                        ).total_seconds(),
                    )
                    if delay_seconds == 0:
                        delay_seconds = OVERDUE_RECHECK_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry time is recalculated
                delay_seconds = DUE_RESOLUTION_RETRY_SECONDS
                self.logger.warning(
                    "scheduler_event=next_due_failed scheduler_id=%s task_id=%s error_type=%s",
                    self._scheduler_id,
                    task_id,
                    type(exc).__name__,
                )

            self.logger.info(
                "scheduler_event=planned_due scheduler_id=%s task_id=%s due_at=%s delay_seconds=%.3f",
                self._scheduler_id,
                task_id,
                due_at.isoformat() if due_at is not None else "none",
                delay_seconds,
            )
            self.logger.info(
                "scheduler_event=sleep_start scheduler_id=%s task_id=%s delay_seconds=%.3f",
                self._scheduler_id,
                task_id,
                delay_seconds,
            )
            wake_reason = await self._wait_for_wake_or_timeout(delay_seconds)
            self.logger.info(
                "scheduler_event=sleep_end scheduler_id=%s task_id=%s wake_reason=%s",
                self._scheduler_id,
                task_id,
                wake_reason,
            )

    async def _wait_for_wake_or_timeout(self, seconds: float) -> str:
        sleeper = asyncio.create_task(self.sleep(max(0.0, seconds)))
        wake = asyncio.create_task(self._wake_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {sleeper, wake}, return_when=asyncio.FIRST_COMPLETED
            )
            return (
                "manual_wake" if wake in done and self._wake_event.is_set() else "timer"
            )
        finally:
            for task in (sleeper, wake):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper, wake, return_exceptions=True)


__all__ = ["LawAssistantScheduler"]
