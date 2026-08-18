from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from fortune_intel.storage import AcquisitionTaskSeed, JobRepository


BASE = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def seeds(count: int, *, max_attempts: int = 5) -> list[AcquisitionTaskSeed]:
    return [
        AcquisitionTaskSeed(
            company_id=index,
            company_name=f"Company {index}",
            stage="discover_sources",
            company_snapshot={"id": index, "name": f"Company {index}", "cik": str(index)},
            stage_snapshot={"verified_seed": f"https://company-{index}.example/"},
            max_attempts=max_attempts,
        )
        for index in range(1, count + 1)
    ]


def repository_with_plan(tmp_path, count=3, *, max_attempts=5):
    repository = JobRepository(tmp_path / "queue.db")
    repository.initialize()
    plan_id = repository.create_acquisition_plan(
        "verified-domain-census",
        seeds(count, max_attempts=max_attempts),
        actor="planner@example.org",
        created_at=BASE,
    )
    return repository, plan_id


def test_plan_and_task_ids_are_stable_and_snapshots_are_frozen(tmp_path):
    repository = JobRepository(tmp_path / "stable.db")
    repository.initialize()
    company_snapshot = {"id": 7, "name": "Example", "website": "https://example.com/"}
    task = AcquisitionTaskSeed(
        7,
        "Example",
        "filing_website",
        company_snapshot=company_snapshot,
        stage_snapshot={"form": "10-K"},
    )

    first = repository.create_acquisition_plan(
        "annual filing sweep", [task], actor="planner", created_at=BASE
    )
    second = repository.create_acquisition_plan(
        "annual filing sweep", [task], actor="another-planner", created_at=BASE + timedelta(days=1)
    )
    original_task = repository.list_acquisition_tasks(first)[0]
    company_snapshot["website"] = "https://changed.example/"

    assert first == second
    assert original_task["id"] == repository.list_acquisition_tasks(second)[0]["id"]
    assert repository.list_acquisition_tasks(first)[0]["company_snapshot"]["website"] == (
        "https://example.com/"
    )


def test_concurrent_claimers_never_receive_the_same_task(tmp_path):
    repository, plan_id = repository_with_plan(tmp_path, count=6)
    barrier = Barrier(2)

    def claim(owner):
        barrier.wait()
        independent = JobRepository(repository.database_path)
        return independent.claim_acquisition_tasks(
            plan_id,
            lease_owner=owner,
            limit=3,
            lease_seconds=60,
            now=BASE,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(claim, ("worker-a", "worker-b"))

    first_ids = {task["id"] for task in first}
    second_ids = {task["id"] for task in second}
    assert len(first_ids) == len(second_ids) == 3
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {
        task["id"] for task in repository.list_acquisition_tasks(plan_id)
    }


def test_expired_lease_is_reclaimed_after_worker_crash(tmp_path):
    repository, plan_id = repository_with_plan(tmp_path, count=1)
    first = repository.claim_acquisition_tasks(
        plan_id, lease_owner="crashed", lease_seconds=30, now=BASE
    )[0]

    assert (
        repository.claim_acquisition_tasks(
            plan_id,
            lease_owner="early",
            lease_seconds=30,
            now=BASE + timedelta(seconds=29),
        )
        == []
    )
    recovered = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner="recovery",
        lease_seconds=30,
        now=BASE + timedelta(seconds=31),
    )[0]

    assert recovered["id"] == first["id"]
    assert recovered["attempts"] == 2
    assert recovered["lease_owner"] == "recovery"
    with pytest.raises(ValueError, match="not leased by this owner"):
        repository.complete_acquisition_task(
            first["id"],
            lease_owner="crashed",
            outcome_code="ok",
            now=BASE + timedelta(seconds=32),
        )


def test_claim_complete_batches_process_every_task_without_skips(tmp_path):
    repository, plan_id = repository_with_plan(tmp_path, count=7)
    completed = []
    while True:
        batch = repository.claim_acquisition_tasks(
            plan_id,
            lease_owner="worker",
            limit=2,
            lease_seconds=60,
            now=BASE,
        )
        if not batch:
            break
        for task in batch:
            completed.append(task["id"])
            repository.complete_acquisition_task(
                task["id"],
                lease_owner="worker",
                outcome_code="verified",
                outcome={"records": 1},
                now=BASE + timedelta(seconds=1),
            )

    assert len(completed) == len(set(completed)) == 7
    assert {task["status"] for task in repository.list_acquisition_tasks(plan_id)} == {"completed"}
    status = repository.acquisition_plan_status(plan_id, now=BASE + timedelta(seconds=2))
    assert status["status"] == "completed"
    assert status["counts"] == {"pending": 0, "leased": 0, "completed": 7, "failed": 0}


def test_retry_uses_bounded_backoff_and_stops_at_max_attempts(tmp_path):
    repository, plan_id = repository_with_plan(tmp_path, count=1, max_attempts=3)
    task = repository.claim_acquisition_tasks(
        plan_id, lease_owner="worker", lease_seconds=300, now=BASE
    )[0]
    first_failure = repository.fail_acquisition_task(
        task["id"],
        lease_owner="worker",
        outcome_code="network_timeout",
        retryable=True,
        error_summary="temporary",
        now=BASE + timedelta(seconds=1),
    )

    assert first_failure["status"] == "pending"
    assert first_failure["next_attempt_at"] == (BASE + timedelta(seconds=61)).isoformat()
    assert (
        repository.claim_acquisition_tasks(
            plan_id,
            lease_owner="worker",
            lease_seconds=300,
            now=BASE + timedelta(seconds=60),
        )
        == []
    )
    second = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner="worker",
        lease_seconds=300,
        now=BASE + timedelta(seconds=61),
    )[0]
    second_failure = repository.fail_acquisition_task(
        second["id"],
        lease_owner="worker",
        outcome_code="http_503",
        retryable=True,
        now=BASE + timedelta(seconds=62),
    )
    assert second_failure["next_attempt_at"] == (BASE + timedelta(seconds=182)).isoformat()
    third = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner="worker",
        lease_seconds=300,
        now=BASE + timedelta(seconds=182),
    )[0]
    final = repository.fail_acquisition_task(
        third["id"],
        lease_owner="worker",
        outcome_code="http_503",
        retryable=True,
        now=BASE + timedelta(seconds=183),
    )

    assert final["status"] == "failed"
    assert final["retryable"] is False
    assert final["next_attempt_at"] is None
    assert (
        repository.acquisition_plan_status(plan_id, now=BASE + timedelta(seconds=184))["status"]
        == "failed"
    )


def test_final_crashed_attempt_is_failed_instead_of_stuck_leased(tmp_path):
    repository, plan_id = repository_with_plan(tmp_path, count=1, max_attempts=1)
    repository.claim_acquisition_tasks(plan_id, lease_owner="crashed", lease_seconds=30, now=BASE)

    assert (
        repository.claim_acquisition_tasks(
            plan_id,
            lease_owner="recovery",
            lease_seconds=30,
            now=BASE + timedelta(seconds=31),
        )
        == []
    )
    task = repository.list_acquisition_tasks(plan_id)[0]
    assert task["status"] == "failed"
    assert task["outcome_code"] == "lease_expired"
    assert task["retryable"] is False


def test_claim_can_be_restricted_to_one_stage(tmp_path):
    repository = JobRepository(tmp_path / "stages.db")
    repository.initialize()
    plan_id = repository.create_acquisition_plan(
        "mixed",
        [
            AcquisitionTaskSeed(1, "One", "website"),
            AcquisitionTaskSeed(1, "One", "discovery"),
        ],
        actor="planner",
        created_at=BASE,
    )

    claimed = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner="website-worker",
        stage="website",
        lease_seconds=60,
        now=BASE,
    )

    assert [task["stage"] for task in claimed] == ["website"]
    assert repository.list_acquisition_tasks(plan_id, status="pending")[0]["stage"] == "discovery"
