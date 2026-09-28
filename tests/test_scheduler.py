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


class DueAwareSchedulerService:
    def __init__(self) -> None:
        self.due_calls = 0
        self.first_due = asyncio.Event()
        self.second_due = asyncio.Event()

    async def process_due_work(self, *, now=None):
        self.due_calls += 1
        self.first_due.set()
        if self.due_calls > 1:
            self.second_due.set()

    def next_due_at(self, now=None):
        return None


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


@pytest.mark.asyncio
async def test_wake_starts_one_due_loop_and_recomputes_without_enabling_scans():
    service = DueAwareSchedulerService()

    async def sleep_forever(_seconds: float) -> None:
        await asyncio.Event().wait()

    scheduler = LawAssistantScheduler(
        service,
        enabled=False,
        interval_minutes=5,
        sleep=sleep_forever,
    )
    await scheduler.start()
    assert scheduler.task is None
    wake = getattr(scheduler, "wake", None)
    assert callable(wake), "scheduler needs a dynamic due-work wake entrypoint"
    if not callable(wake):
        return

    await scheduler.wake()
    await asyncio.wait_for(service.first_due.wait(), timeout=1)
    due_task = scheduler.due_task
    await scheduler.start()
    await scheduler.wake()
    await asyncio.wait_for(service.second_due.wait(), timeout=1)
    assert service.due_calls == 2
    assert scheduler.due_task is due_task
    assert scheduler.scan_task is None

    await scheduler.stop()
    assert due_task is not None and due_task.done()


@pytest.mark.asyncio
async def test_due_loop_runs_immediately_and_does_not_wait_for_scan_interval():
    service = DueAwareSchedulerService()

    async def sleep_forever(_seconds: float) -> None:
        await asyncio.Event().wait()

    scheduler = LawAssistantScheduler(
        service,
        enabled=True,
        interval_minutes=999,
        sleep=sleep_forever,
    )
    await scheduler.start()

    await asyncio.wait_for(service.first_due.wait(), timeout=1)
    assert service.due_calls == 1

    await scheduler.stop()
