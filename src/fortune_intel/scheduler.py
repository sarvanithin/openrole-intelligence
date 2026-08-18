"""Single-instance scheduler loop for registered public ATS sources."""

from __future__ import annotations

import asyncio
import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fortune_intel.observability import log_event
from fortune_intel.services.source_sync import sync_due_sources
from fortune_intel.storage import JobRepository


@contextmanager
def scheduler_lock(database_path: str | Path) -> Iterator[None]:
    lock_path = Path(database_path).with_suffix(".scheduler.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another scheduler instance already holds the lock") from error
        yield


async def scheduler_loop(
    repository: JobRepository,
    *,
    poll_seconds: int = 60,
    concurrency: int = 4,
    batch_size: int = 100,
) -> None:
    if not 10 <= poll_seconds <= 3600:
        raise ValueError("poll_seconds must be between 10 and 3600")
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    log_event(
        "scheduler_started",
        poll_seconds=poll_seconds,
        concurrency=concurrency,
        batch_size=batch_size,
    )
    while True:
        results = await drain_due_sources(
            repository, batch_size=batch_size, concurrency=concurrency
        )
        if results:
            log_event("scheduler_cycle_finished", sources=len(results))
        await asyncio.sleep(poll_seconds)


async def drain_due_sources(
    repository: JobRepository,
    *,
    batch_size: int = 100,
    concurrency: int = 4,
) -> list[dict[str, object]]:
    """Drain every source currently due before the scheduler sleeps."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    results: list[dict[str, object]] = []
    while True:
        batch = await sync_due_sources(repository, limit=batch_size, concurrency=concurrency)
        results.extend(batch)
        if len(batch) < batch_size:
            return results
