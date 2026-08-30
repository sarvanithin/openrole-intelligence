from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from fortune_intel.importers.wikidata_websites import (
    WikidataQueryResult,
    WikidataWebsite,
)
from fortune_intel.services.acquisition_planning import create_acquisition_plan
from fortune_intel.services.acquisition_worker import run_acquisition_worker
from fortune_intel.storage import AcquisitionTaskSeed, JobRepository


BASE = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


class FakeWikidataClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.queries = []

    def query(self, ciks):
        self.queries.append(list(ciks))
        if self.error:
            raise self.error
        return self.result


def wikidata_result(cik="0000000001"):
    return WikidataQueryResult(
        candidates=(
            WikidataWebsite(
                sec_cik=cik,
                item_url="https://www.wikidata.org/entity/Q1",
                website_url="https://www.example.com/",
                career_url="https://www.example.com/careers",
            ),
        ),
        pages_requested=1,
        invalid_bindings=0,
    )


def direct_plan(repository, *, stage, snapshot, created_at=BASE):
    return repository.create_acquisition_plan(
        f"{stage}-plan",
        [
            AcquisitionTaskSeed(
                1,
                "Example",
                stage,
                company_snapshot={"id": 1, "name": "Example", "sec_cik": "0000000001"},
                stage_snapshot=snapshot,
            )
        ],
        actor="planner",
        created_at=created_at,
    )


def test_plan_freezes_only_exact_missing_websites_and_verified_unsupported_seeds(tmp_path):
    repository = JobRepository(tmp_path / "planning.db")
    repository.initialize()
    missing_id = repository.upsert_company("Missing", sec_cik="1")
    verified_id = repository.upsert_company(
        "Verified",
        sec_cik="2",
        website_url="https://verified.example/",
        career_url="https://verified.example/careers",
    )
    repository.set_company_disposition(
        verified_id,
        "unsupported",
        reason=(
            "Canonical website seed verified from https://sec.example/evidence; "
            "reviewed career URL https://verified.example/careers"
        ),
        actor="reviewer",
    )
    supported_id = repository.upsert_company(
        "Supported", sec_cik="3", website_url="https://supported.example/"
    )
    repository.set_company_disposition(
        supported_id,
        "supported",
        reason="Canonical website seed verified from https://sec.example/evidence",
        actor="reviewer",
    )
    repository.upsert_company("Unverified", sec_cik="4", website_url="https://unknown.example/")

    plan_id = create_acquisition_plan(
        repository,
        name="full census",
        scope="all",
        actor="planner",
    )
    tasks = repository.list_acquisition_tasks(plan_id)

    assert {(task["company_id"], task["stage"]) for task in tasks} == {
        (missing_id, "website"),
        (verified_id, "discovery"),
    }
    discovery = next(task for task in tasks if task["stage"] == "discovery")
    assert discovery["stage_snapshot"]["website_url"] == "https://verified.example/"
    assert discovery["stage_snapshot"]["career_url"] == "https://verified.example/careers"
    assert discovery["stage_snapshot"]["verification_events"][0]["actor"] == "reviewer"


def test_website_worker_queries_only_frozen_exact_cik_and_commits_immediately(tmp_path):
    repository = JobRepository(tmp_path / "website.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    plan_id = direct_plan(
        repository,
        stage="website",
        snapshot={"identity_method": "exact_sec_cik", "sec_cik": "0000000001"},
    )
    client = FakeWikidataClient(wikidata_result())

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="website",
        lease_owner="website-1",
        lease_seconds=60,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        now=BASE,
        wikidata_client_factory=lambda _: client,
    )

    assert client.queries == [["0000000001"]]
    assert summary["completed"] == 1
    company = repository.find_company_by_normalized_name("Example")
    assert company["website_url"] == "https://www.example.com/"
    assert repository.list_acquisition_tasks(plan_id)[0]["outcome_code"] == "website_verified"
    event = repository.company_coverage_events(1)[0]
    assert "exact SEC CIK 0000000001" in event["reason"]
    assert "P5531" in event["reason"]


def test_worker_defers_a_task_when_its_final_write_is_locked(tmp_path, monkeypatch):
    repository = JobRepository(tmp_path / "locked-write.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    plan_id = direct_plan(
        repository,
        stage="website",
        snapshot={"identity_method": "exact_sec_cik", "sec_cik": "0000000001"},
    )

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "complete_acquisition_task", locked)
    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="website",
        lease_owner="website-1",
        lease_seconds=60,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        now=BASE,
        wikidata_client_factory=lambda _: FakeWikidataClient(wikidata_result()),
    )

    assert summary["write_deferred"] == 1
    assert summary["completed"] == 0
    assert repository.list_acquisition_tasks(plan_id)[0]["status"] == "leased"


def test_retryable_website_failure_waits_for_backoff_then_succeeds(tmp_path):
    repository = JobRepository(tmp_path / "retry.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    plan_id = direct_plan(
        repository,
        stage="website",
        snapshot={"identity_method": "exact_sec_cik", "sec_cik": "0000000001"},
    )
    failing = FakeWikidataClient(error=RuntimeError("temporary WDQS outage"))

    first = run_acquisition_worker(
        repository,
        plan_id,
        stage="website",
        lease_owner="website-1",
        lease_seconds=60,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        now=BASE,
        wikidata_client_factory=lambda _: failing,
    )
    task = repository.list_acquisition_tasks(plan_id)[0]
    due = datetime.fromisoformat(task["next_attempt_at"])
    early = run_acquisition_worker(
        repository,
        plan_id,
        stage="website",
        lease_owner="website-2",
        lease_seconds=60,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        now=due - timedelta(seconds=1),
        wikidata_client_factory=lambda _: FakeWikidataClient(wikidata_result()),
    )
    successful_client = FakeWikidataClient(wikidata_result())
    recovered = run_acquisition_worker(
        repository,
        plan_id,
        stage="website",
        lease_owner="website-2",
        lease_seconds=60,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        now=due,
        wikidata_client_factory=lambda _: successful_client,
    )

    assert first["retry_scheduled"] == 1
    assert early["claimed"] == 0
    assert recovered["completed"] == 1
    assert repository.list_acquisition_tasks(plan_id)[0]["attempts"] == 2


def test_discovery_worker_uses_frozen_verified_seed(tmp_path):
    repository = JobRepository(tmp_path / "discovery.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    plan_id = direct_plan(
        repository,
        stage="discovery",
        snapshot={"website_url": "https://verified.example/", "career_url": ""},
    )
    received = []

    def discovery_runner(repo, companies, **kwargs):
        assert repo is repository
        received.extend(companies)
        assert kwargs["actor"] == "acquisition-worker:discover-1"
        return [
            {
                "company_id": 1,
                "company_name": "Example",
                "disposition": "unsupported",
                "candidate_ids": [],
                "fingerprint_ids": [],
                "pages_checked": 1,
                "seed_urls_checked": 1,
            }
        ]

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="discover-1",
        lease_seconds=60,
        now=BASE,
        discovery_runner=discovery_runner,
    )

    assert received[0]["website_url"] == "https://verified.example/"
    assert summary["completed"] == 1
    assert repository.list_acquisition_tasks(plan_id)[0]["outcome_code"] == "no_supported_ats"


def test_worker_crash_leaves_lease_for_later_recovery(tmp_path):
    repository = JobRepository(tmp_path / "crash.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    plan_id = direct_plan(
        repository,
        stage="discovery",
        snapshot={"website_url": "https://verified.example/", "career_url": ""},
    )

    def crash(*args, **kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_acquisition_worker(
            repository,
            plan_id,
            stage="discovery",
            lease_owner="crashed",
            lease_seconds=30,
            now=BASE,
            discovery_runner=crash,
        )
    leased = repository.list_acquisition_tasks(plan_id)[0]
    assert leased["status"] == "leased"

    def recovered(*args, **kwargs):
        return [
            {
                "disposition": "unsupported",
                "candidate_ids": [],
                "fingerprint_ids": [],
                "company_id": 1,
                "company_name": "Example",
                "pages_checked": 1,
                "seed_urls_checked": 1,
            }
        ]

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="recovery",
        lease_seconds=30,
        now=BASE + timedelta(seconds=31),
        discovery_runner=recovered,
    )

    assert summary["completed"] == 1
    assert repository.list_acquisition_tasks(plan_id)[0]["attempts"] == 2


def test_transient_discovery_block_is_retryable(tmp_path):
    repository = JobRepository(tmp_path / "discovery-retry.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    plan_id = direct_plan(
        repository,
        stage="discovery",
        snapshot={"website_url": "https://verified.example/", "career_url": ""},
    )

    def blocked(repo, companies, **kwargs):
        repo.set_company_disposition(
            1,
            "blocked",
            reason="Discovery blocked for one or more verified seeds: fetch_failed",
            actor="test",
        )
        return [
            {
                "disposition": "blocked",
                "candidate_ids": [],
                "fingerprint_ids": [],
                "company_id": 1,
                "company_name": "Example",
                "pages_checked": 1,
                "seed_urls_checked": 1,
            }
        ]

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="retry-worker",
        lease_seconds=60,
        now=BASE,
        discovery_runner=blocked,
    )

    assert summary["retry_scheduled"] == 1
    task = repository.list_acquisition_tasks(plan_id)[0]
    assert task["status"] == "pending"
    assert task["outcome_code"] == "discovery_blocked"
    assert task["retryable"] is True


def test_one_hundred_website_tasks_use_one_batched_wikidata_query(tmp_path):
    repository = JobRepository(tmp_path / "website-batch.db")
    repository.initialize()
    task_seeds = []
    for index in range(1, 101):
        cik = str(index).zfill(10)
        repository.upsert_company(f"Company {index}", sec_cik=cik)
        task_seeds.append(
            AcquisitionTaskSeed(
                index,
                f"Company {index}",
                "website",
                company_snapshot={"id": index, "name": f"Company {index}", "sec_cik": cik},
                stage_snapshot={"identity_method": "exact_sec_cik", "sec_cik": cik},
            )
        )
    plan_id = repository.create_acquisition_plan(
        "hundred websites", task_seeds, actor="planner", created_at=BASE
    )
    client = FakeWikidataClient(
        WikidataQueryResult(candidates=(), pages_requested=1, invalid_bindings=0)
    )

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="website",
        lease_owner="batch-worker",
        limit=100,
        lease_seconds=300,
        wikimedia_user_agent="FortuneJobs/1.0 operator@example.org",
        now=BASE,
        wikidata_client_factory=lambda _: client,
    )

    assert len(client.queries) == 1
    assert len(client.queries[0]) == 100
    assert len(set(client.queries[0])) == 100
    assert summary["claimed"] == summary["completed"] == 100


@pytest.mark.parametrize(
    ("disposition", "outcome_code"),
    [("candidate", "current_candidate"), ("unsupported", "current_unsupported")],
)
def test_recent_completed_discovery_is_reused_without_network(tmp_path, disposition, outcome_code):
    repository = JobRepository(tmp_path / f"recent-{disposition}.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    repository.set_company_disposition(1, disposition, reason="recent census", actor="census")
    repository.mark_company_discovered(1, discovered_at=BASE.isoformat())
    plan_id = direct_plan(
        repository,
        stage="discovery",
        snapshot={"website_url": "https://verified.example/", "career_url": ""},
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("recent completed discovery must not be repeated")

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="reuse-worker",
        lease_seconds=60,
        now=BASE + timedelta(minutes=1),
        discovery_runner=unexpected,
    )

    assert summary["completed"] == 1
    assert repository.list_acquisition_tasks(plan_id)[0]["outcome_code"] == outcome_code


def test_newer_reviewed_seed_forces_discovery_despite_recent_result(tmp_path):
    repository = JobRepository(tmp_path / "new-seed.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    repository.set_company_disposition(1, "unsupported", reason="recent census", actor="census")
    repository.mark_company_discovered(1, discovered_at=BASE.isoformat())
    plan_id = direct_plan(
        repository,
        stage="discovery",
        snapshot={
            "website_url": "https://verified.example/",
            "career_url": "https://verified.example/new-careers",
            "verification_events": [
                {
                    "event_id": 2,
                    "occurred_at": (BASE + timedelta(seconds=30)).isoformat(),
                }
            ],
        },
    )
    calls = []

    def discovery_runner(repo, companies, **kwargs):
        calls.extend(companies)
        return [
            {
                "company_id": 1,
                "company_name": "Example",
                "disposition": "unsupported",
                "candidate_ids": [],
                "fingerprint_ids": [],
                "pages_checked": 1,
                "seed_urls_checked": 1,
            }
        ]

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="new-seed-worker",
        lease_seconds=60,
        now=BASE + timedelta(minutes=1),
        discovery_runner=discovery_runner,
    )

    assert summary["completed"] == 1
    assert calls[0]["career_url"] == "https://verified.example/new-careers"
    assert repository.list_acquisition_tasks(plan_id)[0]["outcome_code"] == "no_supported_ats"


@pytest.mark.parametrize("reason", ["robots_denied", "unsafe_redirect", "rejected_start_url"])
def test_recent_policy_or_safety_block_is_terminal_without_network(tmp_path, reason):
    repository = JobRepository(tmp_path / f"terminal-{reason}.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    repository.set_company_disposition(1, "blocked", reason=reason, actor="census")
    repository.mark_company_discovered(1, discovered_at=BASE.isoformat())
    plan_id = direct_plan(
        repository,
        stage="discovery",
        snapshot={"website_url": "https://verified.example/", "career_url": ""},
    )

    summary = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="terminal-worker",
        lease_seconds=60,
        now=BASE + timedelta(minutes=1),
        discovery_runner=lambda *args, **kwargs: pytest.fail("network should not run"),
    )

    assert summary["failed"] == 1
    task = repository.list_acquisition_tasks(plan_id)[0]
    assert task["status"] == "failed"
    assert task["retryable"] is False


def test_recent_fetch_failure_retries_then_performs_network_on_second_attempt(tmp_path):
    repository = JobRepository(tmp_path / "recent-fetch-failed.db")
    repository.initialize()
    repository.upsert_company("Example", sec_cik="1")
    repository.set_company_disposition(1, "blocked", reason="fetch_failed", actor="census")
    repository.mark_company_discovered(1, discovered_at=BASE.isoformat())
    plan_id = direct_plan(
        repository,
        stage="discovery",
        snapshot={"website_url": "https://verified.example/", "career_url": ""},
    )
    calls = []

    def recovered(*args, **kwargs):
        calls.append(1)
        return [
            {
                "disposition": "unsupported",
                "candidate_ids": [],
                "fingerprint_ids": [],
                "company_id": 1,
                "company_name": "Example",
                "pages_checked": 1,
                "seed_urls_checked": 1,
            }
        ]

    first = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="retry-worker",
        lease_seconds=60,
        now=BASE + timedelta(minutes=1),
        discovery_runner=recovered,
    )
    due = datetime.fromisoformat(repository.list_acquisition_tasks(plan_id)[0]["next_attempt_at"])
    second = run_acquisition_worker(
        repository,
        plan_id,
        stage="discovery",
        lease_owner="retry-worker",
        lease_seconds=60,
        now=due,
        discovery_runner=recovered,
    )

    assert first["retry_scheduled"] == 1
    assert calls == [1]
    assert second["completed"] == 1
