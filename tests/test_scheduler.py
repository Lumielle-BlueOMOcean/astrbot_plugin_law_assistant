from __future__ import annotations

import asyncio

import pytest

from scheduler import LawAssistantScheduler


class SchedulerService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.error = error

    async def scan_events(self, trigger: str = "manual"):
        self.calls.append(trigger)
        if self.error:
            error, self.error = self.error, None
            raise error


@pytest.mark.asyncio
async def test_disabled_scheduler_does_not_create_task() -> None:
    scheduler = LawAssistantScheduler(
        SchedulerService(), enabled=False, interval_minutes=5
    )

    await scheduler.start()

    assert scheduler.task is None


@pytest.mark.asyncio
async def test_enabled_scheduler_can_start_and_stop_cleanly() -> None:
    service = SchedulerService()
    wait_forever = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await wait_forever.wait()

    scheduler = LawAssistantScheduler(
        service, enabled=True, interval_minutes=5, sleep=sleep
    )
    await scheduler.start()
    task = scheduler.task
    await scheduler.stop()

    assert task is not None and task.done()
    assert scheduler.task is None


@pytest.mark.asyncio
async def test_scheduler_survives_one_scan_exception() -> None:
    service = SchedulerService(error=RuntimeError("one round failed"))
    rounds = 0
    keep_running = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        nonlocal rounds
        rounds += 1
        if rounds > 1:
            await keep_running.wait()

    scheduler = LawAssistantScheduler(
        service, enabled=True, interval_minutes=5, sleep=sleep
    )
    await scheduler.start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await scheduler.stop()

    assert service.calls == ["scheduler"]
