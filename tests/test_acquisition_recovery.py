from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fortune_intel.services.acquisition_recovery import create_acquisition_recovery_plan
from fortune_intel.storage import AcquisitionTaskSeed, JobRepository

BASE = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _discovery_seed(company_id: int, name: str, url: str) -> AcquisitionTaskSeed:
    return AcquisitionTaskSeed(
        company_id,
        name,
        "discovery",
        company_snapshot={"id": company_id, "name": name, "website_url": url},
        stage_snapshot={
            "website_url": url,
            "career_url": "",
            "verification_events": [{"event_id": company_id}],
        },
        max_attempts=1,
    )


def test_recovery_plan_copies_only_verified_transient_failures(tmp_path):
    repository = JobRepository(tmp_path / "recovery-plan.db")
    repository.initialize()
    transient_id = repository.upsert_company("Transient", website_url="https://transient.example/")
    policy_id = repository.upsert_company("Policy", website_url="https://policy.example/")
    source_plan = repository.create_acquisition_plan(
        "failed acquisition",
        [
            _discovery_seed(transient_id, "Transient", "https://transient.example/"),
            _discovery_seed(policy_id, "Policy", "https://policy.example/"),
        ],
        actor="planner",
        created_at=BASE,
    )
    claimed = repository.claim_acquisition_tasks(
        source_plan, lease_owner="worker", limit=2, lease_seconds=60, now=BASE
    )
    for task in claimed:
        transient = task["company_id"] == transient_id
        repository.fail_acquisition_task(
            task["id"],
            lease_owner="worker",
            outcome_code="discovery_blocked" if transient else "current_discovery_blocked",
            retryable=transient,
            error_summary=(
                "Discovery blocked: fetch_failed"
                if transient
                else "Discovery blocked: robots_denied"
            ),
            now=BASE + timedelta(seconds=1),
        )

    recovery_plan = create_acquisition_recovery_plan(
        repository, source_plan, name="transient recovery", actor="operator"
    )

    recovered = repository.list_acquisition_tasks(recovery_plan)
    source_transient = next(task for task in claimed if task["company_id"] == transient_id)
    assert len(recovered) == 1
    assert recovered[0]["company_id"] == transient_id
    assert recovered[0]["status"] == "pending"
    assert recovered[0]["attempts"] == 0
    assert recovered[0]["stage_snapshot"] == source_transient["stage_snapshot"]
    assert {task["status"] for task in repository.list_acquisition_tasks(source_plan)} == {"failed"}


def test_recovery_plan_rejects_transient_code_without_verified_snapshot(tmp_path):
    repository = JobRepository(tmp_path / "unverified-recovery.db")
    repository.initialize()
    repository.upsert_company("Example", website_url="https://unverified.example/")
    source_plan = repository.create_acquisition_plan(
        "unverified",
        [
            AcquisitionTaskSeed(
                1,
                "Example",
                "discovery",
                stage_snapshot={
                    "website_url": "https://unverified.example/",
                    "career_url": "",
                },
                max_attempts=1,
            )
        ],
        actor="planner",
        created_at=BASE,
    )
    task = repository.claim_acquisition_tasks(
        source_plan, lease_owner="worker", lease_seconds=60, now=BASE
    )[0]
    repository.fail_acquisition_task(
        task["id"],
        lease_owner="worker",
        outcome_code="network_timeout",
        retryable=False,
        error_summary="timeout",
        now=BASE + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="no verified transient"):
        create_acquisition_recovery_plan(
            repository, source_plan, name="unsafe recovery", actor="operator"
        )


def test_recovery_plan_accepts_exact_cik_timeout_and_honors_stage_filter(tmp_path):
    repository = JobRepository(tmp_path / "website-recovery.db")
    repository.initialize()
    company_id = repository.upsert_company("Example", sec_cik="1")
    source_plan = repository.create_acquisition_plan(
        "failed website",
        [
            AcquisitionTaskSeed(
                company_id,
                "Example",
                "website",
                company_snapshot={
                    "id": company_id,
                    "name": "Example",
                    "sec_cik": "0000000001",
                },
                stage_snapshot={
                    "identity_method": "exact_sec_cik",
                    "sec_cik": "0000000001",
                    "wikidata_properties": ["P5531", "P856", "P10311"],
                },
                max_attempts=1,
            )
        ],
        actor="planner",
        created_at=BASE,
    )
    task = repository.claim_acquisition_tasks(
        source_plan, lease_owner="worker", lease_seconds=60, now=BASE
    )[0]
    repository.fail_acquisition_task(
        task["id"],
        lease_owner="worker",
        outcome_code="worker_exception",
        retryable=True,
        error_summary="TimeoutError: temporary WDQS outage",
        now=BASE + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="no verified transient"):
        create_acquisition_recovery_plan(
            repository,
            source_plan,
            name="wrong stage",
            actor="operator",
            stage="discovery",
        )
    recovery_plan = create_acquisition_recovery_plan(
        repository,
        source_plan,
        name="website recovery",
        actor="operator",
        stage="website",
    )

    recovered = repository.list_acquisition_tasks(recovery_plan)
    assert len(recovered) == 1
    assert recovered[0]["stage"] == "website"
    assert recovered[0]["stage_snapshot"] == task["stage_snapshot"]
