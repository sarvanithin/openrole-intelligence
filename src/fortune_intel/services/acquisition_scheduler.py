"""Recurring, lease-safe discovery over frozen verified company URL seeds."""

from __future__ import annotations

import fcntl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fortune_intel.observability import log_event
from fortune_intel.services.acquisition_planning import create_acquisition_plan
from fortune_intel.services.acquisition_worker import run_acquisition_worker
from fortune_intel.storage import JobRepository

DEFAULT_CADENCE_SECONDS = 86_400
_MIN_CADENCE_SECONDS = 3_600
_MAX_CADENCE_SECONDS = 604_800
_MAX_RETRY_WAIT_SECONDS = 21_600


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _validate_limits(
    *, cadence_seconds: int, batch_size: int, lease_seconds: int, max_batches: int
) -> None:
    if not _MIN_CADENCE_SECONDS <= cadence_seconds <= _MAX_CADENCE_SECONDS:
        raise ValueError("cadence_seconds must be between 3600 and 604800")
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    if not 30 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 30 and 3600")
    if not 1 <= max_batches <= 1000:
        raise ValueError("max_batches must be between 1 and 1000")


def _plan_name(now: datetime, cadence_seconds: int) -> str:
    if now.tzinfo is None:
        raise ValueError("scheduler clock must include a timezone")
    bucket = int(now.astimezone(UTC).timestamp()) // cadence_seconds
    return f"verified-source-discovery-{cadence_seconds}s-{bucket}"


@contextmanager
def acquisition_scheduler_lock(database_path: str | Path) -> Iterator[None]:
    """Hold the discovery scheduler lock, separate from source sync."""

    lock_path = Path(database_path).with_suffix(".discovery-scheduler.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "another verified discovery scheduler already holds the lock"
            ) from error
        yield


def _retry_delay(status: dict[str, Any], now: datetime) -> float | None:
    value = status.get("next_attempt_at")
    if not value:
        return None
    try:
        due = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if due.tzinfo is None:
        return None
    return max(0.0, (due.astimezone(UTC) - now.astimezone(UTC)).total_seconds())


def run_verified_discovery_cycle(
    repository: JobRepository,
    *,
    cadence_seconds: int = DEFAULT_CADENCE_SECONDS,
    batch_size: int = 50,
    lease_seconds: int = 300,
    max_batches: int = 200,
    actor: str = "verified-discovery-scheduler",
    lease_owner: str = "verified-discovery-scheduler",
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], None] = time.sleep,
    worker: Callable[..., dict[str, object]] = run_acquisition_worker,
) -> dict[str, object]:
    """Create/resume one cadence-bucket plan and drain bounded ready work."""

    _validate_limits(
        cadence_seconds=cadence_seconds,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        max_batches=max_batches,
    )
    if not actor.strip() or not lease_owner.strip():
        raise ValueError("actor and lease_owner are required")
    cycle_now = now()
    cycle_name = _plan_name(cycle_now, cadence_seconds)
    active = repository.active_acquisition_plan_by_name(cycle_name)
    resumed = active is not None
    if active is None:
        try:
            plan_id = create_acquisition_plan(
                repository,
                name=cycle_name,
                scope="discovery",
                actor=actor.strip(),
                created_at=cycle_now,
            )
        except ValueError as error:
            if str(error) != "at least one acquisition task is required":
                raise
            return {
                "cycle_name": cycle_name,
                "status": "no_tasks",
                "plan_id": None,
                "resumed": False,
                "batches": 0,
                "claimed": 0,
            }
    else:
        plan_id = str(active["id"])

    totals = {"claimed": 0, "completed": 0, "retry_scheduled": 0, "failed": 0}
    batches = 0
    for _ in range(max_batches):
        current = now()
        result = worker(
            repository,
            plan_id,
            stage="discovery",
            lease_owner=lease_owner.strip(),
            limit=batch_size,
            lease_seconds=lease_seconds,
            now=current,
        )
        batches += 1
        for key in totals:
            totals[key] += int(result.get(key, 0))
        status = repository.acquisition_plan_status(plan_id, now=current)
        if int(result.get("claimed", 0)):
            continue
        if status["status"] != "active":
            break
        delay = _retry_delay(status, current)
        if delay is None or delay > _MAX_RETRY_WAIT_SECONDS:
            break
        if delay:
            sleep(delay)

    final_status = repository.acquisition_plan_status(plan_id, now=now())
    return {
        "cycle_name": cycle_name,
        "status": str(final_status["status"]),
        "plan_id": plan_id,
        "resumed": resumed,
        "batches": batches,
        **totals,
        "remaining": final_status["counts"],
        "next_attempt_at": final_status["next_attempt_at"],
    }


def verified_discovery_scheduler_loop(
    repository: JobRepository,
    *,
    cadence_seconds: int = DEFAULT_CADENCE_SECONDS,
    batch_size: int = 50,
    lease_seconds: int = 300,
    max_batches: int = 200,
    actor: str = "verified-discovery-scheduler",
    lease_owner: str = "verified-discovery-scheduler",
    now: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run recurring verified discovery; ``max_cycles`` exists for deterministic tests."""

    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        result = run_verified_discovery_cycle(
            repository,
            cadence_seconds=cadence_seconds,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
            max_batches=max_batches,
            actor=actor,
            lease_owner=lease_owner,
            now=now,
            sleep=sleep,
        )
        cycles += 1
        log_event("verified_discovery_cycle_finished", **result)
        if max_cycles is not None and cycles >= max_cycles:
            return
        if result["status"] == "active":
            sleep(10.0)
            continue
        current = now().astimezone(UTC)
        elapsed = int(current.timestamp()) % cadence_seconds
        sleep(float(cadence_seconds - elapsed if elapsed else cadence_seconds))
