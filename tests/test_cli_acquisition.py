from __future__ import annotations

import csv

from fortune_intel import cli_acquisition
from fortune_intel.cli import parser
from fortune_intel.storage import AcquisitionTaskSeed, JobRepository


def test_cli_parses_acquisition_commands(monkeypatch):
    monkeypatch.setenv("WIKIMEDIA_USER_AGENT", "FortuneJobs/1.0 operator@example.org")

    create = parser().parse_args(
        [
            "acquisition-plan-create",
            "--name",
            "nightly",
            "--scope",
            "all",
            "--actor",
            "operator",
        ]
    )
    status = parser().parse_args(["acquisition-plan-status", "ap_example"])
    worker = parser().parse_args(
        [
            "acquisition-worker",
            "ap_example",
            "--stage",
            "website",
            "--lease-owner",
            "worker-1",
        ]
    )
    requeue = parser().parse_args(
        [
            "acquisition-plan-requeue",
            "ap_failed",
            "--name",
            "retry transient",
            "--actor",
            "operator",
            "--stage",
            "discovery",
        ]
    )
    scheduler = parser().parse_args(["run-discovery-scheduler"])
    continuous = parser().parse_args(["run-acquisition-scheduler", "--max-cycles", "1"])

    assert create.scope == "all"
    assert create.companies_csv is None
    assert status.plan == "ap_example"
    assert worker.user_agent == "FortuneJobs/1.0 operator@example.org"
    assert worker.limit == 10
    assert requeue.plan == "ap_failed"
    assert requeue.stage == "discovery"
    assert scheduler.cadence_seconds == 86400
    assert scheduler.batch_size == 50
    assert scheduler.lease_seconds == 300
    assert scheduler.max_batches == 200
    assert continuous.max_cycles == 1


def test_cli_create_and_status_commands_use_durable_plan(tmp_path):
    repository = JobRepository(tmp_path / "cli.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    create = parser().parse_args(
        [
            "acquisition-plan-create",
            "--name",
            "website census",
            "--scope",
            "website",
            "--actor",
            "operator",
        ]
    )

    created = cli_acquisition.run_acquisition_command(create, repository)
    status_args = parser().parse_args(["acquisition-plan-status", created["id"]])
    status = cli_acquisition.run_acquisition_command(status_args, repository)

    assert created["total_tasks"] == 1
    assert status["id"] == created["id"]
    assert status["counts"]["pending"] == 1


def test_cli_create_can_freeze_an_exact_reviewed_csv_batch(tmp_path):
    repository = JobRepository(tmp_path / "targeted-cli.db")
    repository.initialize()
    first_id = repository.upsert_company("First", website_url="https://first.example/")
    repository.set_company_disposition(
        first_id,
        "unreviewed",
        reason="Canonical website seed verified from https://evidence.example/first",
        actor="reviewer",
    )
    second_id = repository.upsert_company("Second", website_url="https://second.example/")
    repository.set_company_disposition(
        second_id,
        "unreviewed",
        reason="Canonical website seed verified from https://evidence.example/second",
        actor="reviewer",
    )
    target_csv = tmp_path / "batch.csv"
    with target_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("company_name",))
        writer.writeheader()
        writer.writerow({"company_name": "Second"})
    args = parser().parse_args(
        [
            "acquisition-plan-create",
            "--name",
            "targeted discovery",
            "--scope",
            "discovery",
            "--actor",
            "operator",
            "--companies-csv",
            str(target_csv),
        ]
    )

    result = cli_acquisition.run_acquisition_command(args, repository)
    tasks = repository.list_acquisition_tasks(result["id"])

    assert result["total_tasks"] == 1
    assert tasks[0]["company_id"] == second_id


def test_cli_requeue_creates_a_new_plan_from_verified_transient_failure(tmp_path):
    repository = JobRepository(tmp_path / "cli-requeue.db")
    repository.initialize()
    company_id = repository.upsert_company("Example", website_url="https://verified.example/")
    source_plan = repository.create_acquisition_plan(
        "failed",
        [
            AcquisitionTaskSeed(
                company_id,
                "Example",
                "discovery",
                company_snapshot={
                    "id": company_id,
                    "name": "Example",
                    "website_url": "https://verified.example/",
                },
                stage_snapshot={
                    "website_url": "https://verified.example/",
                    "career_url": "",
                    "verification_events": [{"event_id": 1}],
                },
                max_attempts=1,
            )
        ],
        actor="planner",
    )
    task = repository.claim_acquisition_tasks(source_plan, lease_owner="worker", lease_seconds=60)[
        0
    ]
    repository.fail_acquisition_task(
        task["id"],
        lease_owner="worker",
        outcome_code="network_timeout",
        retryable=True,
    )
    args = parser().parse_args(
        [
            "acquisition-plan-requeue",
            source_plan,
            "--name",
            "retry transient",
            "--actor",
            "operator",
        ]
    )

    result = cli_acquisition.run_acquisition_command(args, repository)

    assert result["id"] != source_plan
    assert result["source_plan_id"] == source_plan
    assert result["requeued_tasks"] == 1
    assert result["counts"]["pending"] == 1
