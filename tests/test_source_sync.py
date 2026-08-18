from __future__ import annotations

import asyncio

import pytest

from fortune_intel.connectors.models import (
    ConnectorError,
    ConnectorJob,
    ConnectorResult,
)
from fortune_intel.services import source_sync
from fortune_intel.storage import JobRepository


class StubConnector:
    def __init__(self, result):
        self.result = result

    def fetch(self):
        return self.result


@pytest.mark.asyncio
async def test_due_source_sync_uses_a_bounded_sixteen_worker_pool(monkeypatch):
    active = 0
    peak = 0

    class Repository:
        def due_career_sources(self, *, limit):
            return [{"id": index} for index in range(limit)]

    async def fake_sync(_repository, _source):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"source_id": _source["id"], "status": "success"}

    monkeypatch.setattr(source_sync, "sync_registered_source", fake_sync)

    result = await source_sync.sync_due_sources(Repository(), limit=32, concurrency=32)

    assert len(result) == 32
    assert peak == source_sync.MAX_SOURCE_SYNC_CONCURRENCY == 16


def approved_source(repository):
    company_id = repository.upsert_company("Example Company")
    repository.upsert_career_source(
        company_id,
        kind="greenhouse",
        board_token="example",
        base_url="https://boards.greenhouse.io/example",
        terms_url="https://example.test/terms",
        policy_approved_at="2026-08-06T12:00:00Z",
        owner_contact="data@example.test",
    )
    return repository.due_career_sources()[0]


@pytest.mark.asyncio
async def test_complete_registered_source_persists_jobs(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "sync.db")
    repository.initialize()
    source = approved_source(repository)
    result = ConnectorResult(
        source="greenhouse",
        source_key="example",
        jobs=(
            ConnectorJob(
                source="greenhouse",
                external_job_id="job-1",
                title="Data Engineer",
                url="https://boards.greenhouse.io/example/jobs/job-1",
                description="Visa sponsorship is available for this role.",
                source_opened_at="2026-08-05T12:00:00+00:00",
            ),
        ),
        complete=True,
        pages_fetched=1,
    )
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(result))

    outcome = await source_sync.sync_registered_source(repository, source)

    assert outcome == {
        "source_id": source["id"],
        "status": "success",
        "jobs": 1,
        "closed": 0,
        "pages": 1,
    }
    persisted = repository.list_jobs()[0]
    assert persisted["sponsorship_tier"] == "A"
    assert persisted["source_opened_at"] == "2026-08-05T12:00:00+00:00"
    assert persisted["date_provenance"] == "source_opened_at"
    assert repository.source_status()[0]["consecutive_failures"] == 0
    assert repository.get_company_coverage(source["company_id"])["disposition"] == "supported"


@pytest.mark.asyncio
async def test_partial_manifest_never_closes_missing_jobs(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "partial.db")
    repository.initialize()
    source = approved_source(repository)
    complete = ConnectorResult(
        source="greenhouse",
        source_key="example",
        jobs=(
            ConnectorJob("greenhouse", "one", "One", "https://jobs.example.test/one"),
            ConnectorJob("greenhouse", "two", "Two", "https://jobs.example.test/two"),
        ),
        complete=True,
    )
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(complete))
    await source_sync.sync_registered_source(repository, source)

    partial = ConnectorResult(
        source="greenhouse",
        source_key="example",
        jobs=(ConnectorJob("greenhouse", "two", "Two", "https://jobs.example.test/two"),),
        complete=False,
        errors=(ConnectorError("timeout", "later page failed", retryable=True),),
        pages_fetched=1,
    )
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(partial))
    outcome = await source_sync.sync_registered_source(repository, source)

    assert outcome["status"] == "partial_success"
    assert len(repository.list_jobs()) == 2
    assert repository.source_status()[0]["consecutive_failures"] == 1
    assert repository.get_company_coverage(source["company_id"])["disposition"] == "supported"

    await source_sync.sync_registered_source(repository, source)
    assert repository.source_status()[0]["consecutive_failures"] == 2
    assert repository.get_company_coverage(source["company_id"])["disposition"] == "stale"


@pytest.mark.asyncio
async def test_sync_retains_global_manifest_but_exposes_only_us_jobs(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "geography.db")
    repository.initialize()
    source = approved_source(repository)
    result = ConnectorResult(
        source="greenhouse",
        source_key="example",
        jobs=(
            ConnectorJob(
                "greenhouse",
                "us",
                "U.S. role",
                "https://jobs.example.test/us",
                location="Austin, TX",
            ),
            ConnectorJob(
                "greenhouse",
                "ca",
                "Canadian role",
                "https://jobs.example.test/ca",
                location="Toronto, Ontario, Canada",
            ),
        ),
        complete=True,
    )
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(result))

    outcome = await source_sync.sync_registered_source(repository, source)

    assert outcome["jobs"] == 2
    assert repository.count_jobs() == 2
    assert repository.count_jobs(us_eligibility="eligible") == 1
    assert repository.list_jobs(us_eligibility="eligible")[0]["title"] == "U.S. role"


@pytest.mark.asyncio
async def test_verified_empty_sync_enters_normal_closure_grace(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "verified-empty.db")
    repository.initialize()
    source = approved_source(repository)
    populated = ConnectorResult(
        "greenhouse",
        "example",
        (ConnectorJob("greenhouse", "one", "One", "https://jobs.example.test/one"),),
        complete=True,
    )
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(populated))
    await source_sync.sync_registered_source(repository, source)
    job_id = repository.list_jobs()[0]["id"]
    empty = ConnectorResult("greenhouse", "example", (), complete=True, pages_fetched=1)
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(empty))

    first = await source_sync.sync_registered_source(repository, source)
    assert first["status"] == "anomalous_empty"
    assert repository.get_job(job_id)["missed_complete_runs"] == 0
    assert repository.source_status()[0]["consecutive_complete_empty_observations"] == 1

    second = await source_sync.sync_registered_source(repository, source)
    assert second["status"] == "success"
    assert second["complete_empty_observations"] == 2
    assert second["closed"] == 0
    assert repository.get_job(job_id)["missed_complete_runs"] == 1

    third = await source_sync.sync_registered_source(repository, source)
    assert third["status"] == "success"
    assert third["closed"] == 1
    assert repository.get_job(job_id)["status"] == "closed"


@pytest.mark.asyncio
async def test_source_empty_streak_survives_incomplete_and_resets_on_nonempty(
    monkeypatch, tmp_path
):
    repository = JobRepository(tmp_path / "empty-reset.db")
    repository.initialize()
    source = approved_source(repository)
    empty = ConnectorResult("greenhouse", "example", (), complete=True)
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(empty))
    await source_sync.sync_registered_source(repository, source)

    incomplete = ConnectorResult(
        "greenhouse",
        "example",
        (),
        complete=False,
        errors=(ConnectorError("timeout", "probe failed", retryable=True),),
    )
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(incomplete))
    await source_sync.sync_registered_source(repository, source)
    assert repository.source_status()[0]["consecutive_complete_empty_observations"] == 1

    populated = ConnectorResult(
        "greenhouse",
        "example",
        (ConnectorJob("greenhouse", "one", "One", "https://jobs.example.test/one"),),
        complete=True,
    )
    monkeypatch.setattr(source_sync, "build_connector", lambda *_: StubConnector(populated))
    await source_sync.sync_registered_source(repository, source)
    assert repository.source_status()[0]["consecutive_complete_empty_observations"] == 0
