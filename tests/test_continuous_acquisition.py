from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fortune_intel.cli import parser
from fortune_intel.services.acquisition_planning import create_acquisition_plan
from fortune_intel.services.acquisition_worker import run_acquisition_worker
from fortune_intel.services.continuous_acquisition import (
    acquisition_operational_metrics,
    run_continuous_acquisition_cycle,
)
from fortune_intel.storage import JobRepository


BASE = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
POLICY_URL = "https://developers.greenhouse.io/job-board.html"


def _verified_company(repository: JobRepository, name: str, *, cik: str = "1") -> int:
    company_id = repository.upsert_company(
        name,
        sec_cik=cik,
        website_url=f"https://{name.casefold()}.example/",
    )
    repository.set_company_disposition(
        company_id,
        "candidate",
        reason="Canonical website seed verified from https://sec.example/submissions",
        actor="reviewer",
        reviewed_at=BASE.isoformat(),
    )
    return company_id


def _official_candidate(repository: JobRepository, company_id: int, name: str) -> int:
    seed = f"https://{name.casefold()}.example/"
    return repository.upsert_source_candidate(
        company_id,
        candidate_url=f"https://boards.greenhouse.io/{name.casefold()}",
        kind="greenhouse",
        confidence=0.99,
        evidence={
            "board_token": name.casefold(),
            "seed_urls_checked": [seed],
            "candidate_evidence": ["official company career link"],
        },
        terms_status="review_required",
        discovered_at=BASE.isoformat(),
    )


def test_activation_plan_requires_policy_and_verified_primary_provenance(tmp_path):
    repository = JobRepository(tmp_path / "activation-plan.db")
    repository.initialize()
    verified_id = _verified_company(repository, "Verified")
    candidate_id = _official_candidate(repository, verified_id, "Verified")
    unverified_id = _verified_company(repository, "Unverified", cik="2")
    repository.upsert_source_candidate(
        unverified_id,
        candidate_url="https://boards.greenhouse.io/unverified",
        kind="greenhouse",
        confidence=1.0,
        evidence={
            "verification_status": "unverified",
            "activation_allowed": False,
            "seed_urls_checked": ["https://unverified.example/"],
        },
        discovered_at=BASE.isoformat(),
    )

    plan_id = create_acquisition_plan(
        repository,
        name="policy-ready",
        scope="activation",
        actor="operator",
        policy_urls={"greenhouse": POLICY_URL},
        policy_approved_at=BASE.isoformat(),
        created_at=BASE,
    )

    tasks = repository.list_acquisition_tasks(plan_id)
    assert len(tasks) == 1
    assert tasks[0]["company_id"] == verified_id
    assert tasks[0]["stage_snapshot"]["candidate_id"] == candidate_id
    assert tasks[0]["stage_snapshot"]["policy_url"] == POLICY_URL


def test_continuous_cycle_uses_durable_registry_runner_for_discovery_handoff(tmp_path):
    repository = JobRepository(tmp_path / "registry-handoff.db")
    repository.initialize()
    calls: list[dict[str, object]] = []

    def registry_runner(repo, **kwargs):
        assert repo is repository
        calls.append(kwargs)
        return {"status": "drained", "discovery_handoff": {"companies": 1}}

    result = run_continuous_acquisition_cycle(
        repository,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        policy_urls={"greenhouse": POLICY_URL},
        policy_approved_at=BASE.isoformat(),
        batch_size=7,
        registry_runner=registry_runner,
    )

    assert calls == [
        {
            "actor": "continuous-acquisition-scheduler",
            "batch_size": 7,
            "concurrency": 7,
            "max_batches": 1,
            "pace_seconds": 0,
            "policy_urls": {"greenhouse": POLICY_URL},
            "policy_approved_at": BASE.isoformat(),
            "sync_interval_minutes": 60,
        }
    ]
    assert result["registry_career_portal_verification"]["discovery_handoff"] == {"companies": 1}


def test_activation_probe_retries_with_checkpoint_then_activates(tmp_path):
    repository = JobRepository(tmp_path / "activation-retry.db")
    repository.initialize()
    company_id = _verified_company(repository, "Example")
    _official_candidate(repository, company_id, "Example")
    plan_id = create_acquisition_plan(
        repository,
        name="activation-retry",
        scope="activation",
        actor="operator",
        policy_urls={"greenhouse": POLICY_URL},
        policy_approved_at=BASE.isoformat(),
        created_at=BASE,
    )
    calls = []

    def transient_then_success(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise TimeoutError("temporary probe timeout")
        return 77

    first = run_acquisition_worker(
        repository,
        plan_id,
        stage="activation",
        lease_owner="activation-1",
        lease_seconds=60,
        now=BASE,
        activation_runner=transient_then_success,
    )
    task = repository.list_acquisition_tasks(plan_id)[0]
    due = datetime.fromisoformat(task["next_attempt_at"])
    second = run_acquisition_worker(
        repository,
        plan_id,
        stage="activation",
        lease_owner="activation-1",
        lease_seconds=60,
        now=due,
        activation_runner=transient_then_success,
    )

    assert first["retry_scheduled"] == 1
    assert second["completed"] == 1
    completed = repository.list_acquisition_tasks(plan_id)[0]
    assert completed["attempts"] == 2
    assert completed["outcome_code"] == "source_activated"
    assert completed["outcome"] == {"source_id": 77}


def test_exact_reviewed_h1b_company_is_claimed_before_general_company(tmp_path):
    repository = JobRepository(tmp_path / "priority.db")
    repository.initialize()
    general_id = _verified_company(repository, "General", cik="1")
    h1b_id = _verified_company(repository, "Sponsor", cik="2")
    repository.record_sponsorship_fact(
        h1b_id,
        source="DOL",
        fiscal_year=2026,
        lca_worker_positions=25,
        entity_match_confidence=1.0,
        source_url="https://dol.example/data",
        source_document="dol.csv",
        source_checksum="a" * 64,
        match_method="reviewed_exact_legal_name",
    )
    plan_id = create_acquisition_plan(
        repository,
        name="priority",
        scope="discovery",
        actor="operator",
        created_at=BASE,
    )

    first = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner="priority-worker",
        stage="discovery",
        limit=1,
        lease_seconds=60,
        now=BASE,
    )[0]

    assert first["company_id"] == h1b_id
    assert first["company_id"] != general_id
    assert first["stage_snapshot"]["priority_rank"] == 1
    assert "exact_reviewed_h1b" in first["stage_snapshot"]["priority_reasons"]


def test_missing_exact_identity_is_checkpointed_as_a_dead_letter(tmp_path):
    repository = JobRepository(tmp_path / "missing-identity.db")
    repository.initialize()
    repository.upsert_company("No CIK")
    plan_id = create_acquisition_plan(
        repository,
        name="missing-identity",
        scope="website",
        actor="operator",
        created_at=BASE,
    )

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="website",
        lease_owner="website-1",
        lease_seconds=60,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        now=BASE,
        wikidata_client_factory=lambda _: pytest.fail("missing CIK must not be queried"),
    )

    assert summary["failed"] == 1
    task = repository.list_acquisition_tasks(plan_id)[0]
    assert task["outcome_code"] == "identity_unavailable"
    assert task["retryable"] is False
    assert task["stage_snapshot"]["identity_reason"] == "missing_sec_cik"


def test_targeted_batch_does_not_make_duplicate_cik_look_unique(tmp_path):
    repository = JobRepository(tmp_path / "duplicate-cik.db")
    repository.initialize()
    first_id = repository.upsert_company("First", sec_cik="1")
    repository.upsert_company("Second", sec_cik="1")

    plan_id = create_acquisition_plan(
        repository,
        name="targeted-duplicate",
        scope="website",
        actor="operator",
        company_ids={first_id},
        created_at=BASE,
    )

    task = repository.list_acquisition_tasks(plan_id)[0]
    assert task["stage_snapshot"]["identity_method"] == "identity_unavailable"
    assert task["stage_snapshot"]["identity_reason"] == "ambiguous_sec_cik"


def test_continuous_cycle_resumes_each_bounded_stage_and_reports_family_queues(tmp_path):
    repository = JobRepository(tmp_path / "continuous.db")
    repository.initialize()
    company_id = _verified_company(repository, "Example")
    _official_candidate(repository, company_id, "Example")
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://example.icims.com/jobs/search",
        family="icims",
        evidence={
            "review_method": "third_party_discovery_lead",
            "activation_allowed": False,
        },
        actor="inventory",
        observed_at=BASE.isoformat(),
        mark_discovered=False,
    )

    def complete_worker(repo, plan_id, **kwargs):
        claimed = repo.claim_acquisition_tasks(
            plan_id,
            lease_owner=kwargs["lease_owner"],
            stage=kwargs["stage"],
            limit=kwargs["limit"],
            lease_seconds=kwargs["lease_seconds"],
            now=kwargs["now"],
        )
        for task in claimed:
            repo.complete_acquisition_task(
                task["id"],
                lease_owner=kwargs["lease_owner"],
                outcome_code=f"{kwargs['stage']}_complete",
                now=kwargs["now"],
            )
        return {
            "claimed": len(claimed),
            "completed": len(claimed),
            "retry_scheduled": 0,
            "failed": 0,
        }

    result = run_continuous_acquisition_cycle(
        repository,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        policy_urls={"greenhouse": POLICY_URL},
        policy_approved_at=BASE.isoformat(),
        now=lambda: BASE,
        worker=complete_worker,
    )

    assert [stage["stage"] for stage in result["stages"]] == [
        "website",
        "discovery",
        "activation",
    ]
    assert result["stages"][0]["status"] == "no_tasks"
    assert result["stages"][1]["completed"] == 1
    assert result["stages"][2]["completed"] == 1
    assert result["metrics"]["unsupported_family_queue"]["icims"] == {
        "companies": 1,
        "observations": 1,
    }
    queue = result["metrics"]["supported_candidate_queue_by_family"]["greenhouse"]
    assert queue["operator_policy_configured"] is True


def test_metrics_expose_dead_letter_dispositions(tmp_path):
    repository = JobRepository(tmp_path / "dead-letter.db")
    repository.initialize()
    company_id = _verified_company(repository, "Example")
    plan_id = create_acquisition_plan(
        repository,
        name="dead-letter",
        scope="discovery",
        actor="operator",
        created_at=BASE,
    )
    task = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner="worker",
        stage="discovery",
        lease_seconds=60,
        now=BASE,
    )[0]
    repository.fail_acquisition_task(
        task["id"],
        lease_owner="worker",
        outcome_code="robots_denied",
        retryable=False,
        now=BASE + timedelta(seconds=1),
    )

    metrics = acquisition_operational_metrics(repository)

    assert company_id > 0
    assert metrics["checkpoints"]["discovery:failed"] == 1
    assert metrics["dead_letters"]["discovery:robots_denied"] == 1


def test_cli_parses_continuous_scheduler_and_metrics(monkeypatch):
    monkeypatch.setenv("WIKIMEDIA_USER_AGENT", "FortuneJobs/1.0 operator@example.org")

    scheduler = parser().parse_args(
        [
            "run-acquisition-scheduler",
            "--policy",
            f"greenhouse={POLICY_URL}",
            "--policy-approved-at",
            BASE.isoformat(),
        ]
    )
    metrics = parser().parse_args(["acquisition-metrics"])

    assert scheduler.user_agent == "FortuneJobs/1.0 operator@example.org"
    assert scheduler.policy == [f"greenhouse={POLICY_URL}"]
    assert scheduler.poll_seconds == 60
    assert metrics.command == "acquisition-metrics"


def test_continuous_scheduler_requires_contactable_wikimedia_identity(tmp_path):
    repository = JobRepository(tmp_path / "user-agent.db")
    repository.initialize()

    with pytest.raises(ValueError, match="Wikimedia user-agent"):
        run_continuous_acquisition_cycle(repository, wikimedia_user_agent="")
