from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fortune_intel.scheduler import scheduler_lock
from fortune_intel.services.acquisition_scheduler import (
    acquisition_scheduler_lock,
    run_verified_discovery_cycle,
    verified_discovery_scheduler_loop,
)
from fortune_intel.storage import JobRepository

BASE = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, value: datetime = BASE):
        self.value = value
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


def _verified_company(repository: JobRepository, name: str = "Verified") -> int:
    company_id = repository.upsert_company(name, website_url="https://verified.example/")
    repository.set_company_disposition(
        company_id,
        "unsupported",
        reason="Canonical website seed verified from https://sec.example/evidence",
        actor="reviewer",
    )
    return company_id


def _complete_claimed_worker(repository, plan_id, **kwargs):
    claimed = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner=kwargs["lease_owner"],
        stage=kwargs["stage"],
        limit=kwargs["limit"],
        lease_seconds=kwargs["lease_seconds"],
        now=kwargs["now"],
    )
    for task in claimed:
        repository.complete_acquisition_task(
            task["id"],
            lease_owner=kwargs["lease_owner"],
            outcome_code="no_supported_ats",
            now=kwargs["now"],
        )
    return {
        "claimed": len(claimed),
        "completed": len(claimed),
        "retry_scheduled": 0,
        "failed": 0,
    }


def test_discovery_scheduler_lock_is_separate_and_singleton(tmp_path):
    database = tmp_path / "jobs.db"

    with scheduler_lock(database):
        with acquisition_scheduler_lock(database):
            with pytest.raises(RuntimeError, match="verified discovery scheduler"):
                with acquisition_scheduler_lock(database):
                    pass

    assert database.with_suffix(".scheduler.lock").exists()
    assert database.with_suffix(".discovery-scheduler.lock").exists()


def test_empty_verified_seed_set_is_a_normal_no_task_cycle(tmp_path):
    repository = JobRepository(tmp_path / "empty.db")
    repository.initialize()
    repository.upsert_company("Unverified", website_url="https://unverified.example/")

    result = run_verified_discovery_cycle(
        repository,
        now=lambda: BASE,
        sleep=lambda _: pytest.fail("empty cycle must not sleep internally"),
        worker=lambda *args, **kwargs: pytest.fail("empty cycle must not run a worker"),
    )

    assert result["status"] == "no_tasks"
    assert result["plan_id"] is None
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM acquisition_plans").fetchone()[0] == 0


def test_cycle_freezes_only_verified_seeds_and_never_approves_candidates(tmp_path):
    repository = JobRepository(tmp_path / "verified-only.db")
    repository.initialize()
    verified_id = _verified_company(repository)
    repository.upsert_company("Unverified", website_url="https://unverified.example/")

    result = run_verified_discovery_cycle(
        repository,
        now=lambda: BASE,
        sleep=lambda _: None,
        worker=_complete_claimed_worker,
    )

    tasks = repository.list_acquisition_tasks(str(result["plan_id"]))
    assert result["status"] == "completed"
    assert len(tasks) == 1
    assert tasks[0]["company_id"] == verified_id
    assert tasks[0]["stage_snapshot"]["website_url"] == "https://verified.example/"
    assert tasks[0]["stage_snapshot"]["verification_events"]
    assert repository.list_source_candidates(verified_id) == []
    assert repository.source_status() == []


def test_active_plan_is_resumed_and_retry_backoff_is_honored(tmp_path):
    repository = JobRepository(tmp_path / "resume.db")
    repository.initialize()
    _verified_company(repository)
    clock = FakeClock()

    def transient_then_complete(repo, plan_id, **kwargs):
        claimed = repo.claim_acquisition_tasks(
            plan_id,
            lease_owner=kwargs["lease_owner"],
            stage="discovery",
            limit=kwargs["limit"],
            lease_seconds=kwargs["lease_seconds"],
            now=kwargs["now"],
        )
        if not claimed:
            return {"claimed": 0, "completed": 0, "retry_scheduled": 0, "failed": 0}
        task = claimed[0]
        if task["attempts"] == 1:
            repo.fail_acquisition_task(
                task["id"],
                lease_owner=kwargs["lease_owner"],
                outcome_code="discovery_blocked",
                retryable=True,
                error_summary="fetch_failed",
                now=kwargs["now"],
            )
            return {"claimed": 1, "completed": 0, "retry_scheduled": 1, "failed": 0}
        repo.complete_acquisition_task(
            task["id"],
            lease_owner=kwargs["lease_owner"],
            outcome_code="no_supported_ats",
            now=kwargs["now"],
        )
        return {"claimed": 1, "completed": 1, "retry_scheduled": 0, "failed": 0}

    first = run_verified_discovery_cycle(
        repository,
        max_batches=1,
        now=clock.now,
        sleep=clock.sleep,
        worker=transient_then_complete,
    )
    second = run_verified_discovery_cycle(
        repository,
        now=clock.now,
        sleep=clock.sleep,
        worker=transient_then_complete,
    )

    assert first["status"] == "active"
    assert second["resumed"] is True
    assert second["plan_id"] == first["plan_id"]
    assert second["status"] == "completed"
    assert clock.sleeps == [60.0]
    task = repository.list_acquisition_tasks(str(first["plan_id"]))[0]
    assert task["attempts"] == 2


def test_recurring_loop_waits_until_next_cadence_bucket_when_no_tasks(tmp_path):
    repository = JobRepository(tmp_path / "loop.db")
    repository.initialize()
    clock = FakeClock(datetime(2026, 8, 11, 0, 0, tzinfo=UTC))

    verified_discovery_scheduler_loop(
        repository,
        cadence_seconds=86_400,
        now=clock.now,
        sleep=clock.sleep,
        max_cycles=2,
    )

    assert clock.sleeps == [86_400.0]


@pytest.mark.parametrize(
    ("field", "value"),
    [("cadence_seconds", 3599), ("batch_size", 101), ("lease_seconds", 29), ("max_batches", 0)],
)
def test_scheduler_limits_fail_closed(tmp_path, field, value):
    repository = JobRepository(tmp_path / f"invalid-{field}.db")
    repository.initialize()
    kwargs = {field: value, "now": lambda: BASE}

    with pytest.raises(ValueError):
        run_verified_discovery_cycle(repository, **kwargs)
