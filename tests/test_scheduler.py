from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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

    async def process_due_work(self, *, now=None, task_id=None):
        self.due_calls += 1
        self.first_due.set()
        if self.due_calls > 1:
            self.second_due.set()

    def next_due_at(self, now=None):
        return None


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class ClockedDueService:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[datetime] = []
        self.called = asyncio.Event()

    async def process_due_work(self, *, now=None, task_id=None):
        self.calls.append(now)
        self.called.set()

    def next_due_at(self, now=None):
        local_now = (now or self.clock()).astimezone(ZoneInfo("Asia/Shanghai"))
        due = local_now.replace(hour=16, minute=30, second=0, microsecond=0)
        if local_now >= due:
            due += timedelta(days=1)
        return due


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


@pytest.mark.asyncio
async def test_due_scheduler_runs_at_1630_when_started_at_due_time(caplog):
    clock = Clock(datetime(2026, 10, 9, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    service = ClockedDueService(clock)
    sleep_requested = asyncio.Queue()
    release_sleep = asyncio.Event()

    async def controlled_sleep(seconds: float) -> None:
        await sleep_requested.put(seconds)
        await release_sleep.wait()

    caplog.set_level("INFO", logger="scheduler")
    scheduler = LawAssistantScheduler(
        service,
        enabled=True,
        scan_enabled=False,
        interval_minutes=5,
        sleep=controlled_sleep,
        clock=clock,
    )
    await scheduler.start()
    try:
        await asyncio.wait_for(service.called.wait(), timeout=1)
        assert service.calls == [clock.now]
        assert await asyncio.wait_for(sleep_requested.get(), timeout=1) == 24 * 60 * 60
        assert "scheduler_event=created" in caplog.text
        assert "scheduler_event=planned_due" in caplog.text
        assert "scheduler_event=sleep_start" in caplog.text
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_due_scheduler_wakes_at_1630_when_started_one_minute_early(caplog):
    clock = Clock(datetime(2026, 10, 9, 16, 29, tzinfo=ZoneInfo("Asia/Shanghai")))
    service = ClockedDueService(clock)
    sleep_requested = asyncio.Queue()
    release_sleep = asyncio.Event()

    async def controlled_sleep(seconds: float) -> None:
        await sleep_requested.put(seconds)
        await release_sleep.wait()
        release_sleep.clear()

    caplog.set_level("INFO", logger="scheduler")
    scheduler = LawAssistantScheduler(
        service,
        enabled=True,
        scan_enabled=False,
        interval_minutes=5,
        sleep=controlled_sleep,
        clock=clock,
    )
    await scheduler.start()
    try:
        await asyncio.wait_for(service.called.wait(), timeout=1)
        assert service.calls == [clock.now]
        assert await asyncio.wait_for(sleep_requested.get(), timeout=1) == 60
        clock.now = datetime(2026, 10, 9, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        service.called.clear()
        release_sleep.set()
        await asyncio.wait_for(service.called.wait(), timeout=1)
        assert service.calls[-1] == clock.now
        assert "wake_reason=timer" in caplog.text
        assert "scheduler_event=sleep_end" in caplog.text
    finally:
        await scheduler.stop()
