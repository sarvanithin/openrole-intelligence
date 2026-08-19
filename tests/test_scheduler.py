import asyncio

from fortune_intel import scheduler
from fortune_intel.cli import parser
from fortune_intel.storage import JobRepository


def test_drain_due_sources_processes_all_batches(monkeypatch):
    batch_sizes = [3, 3, 1]
    calls = []

    async def fake_sync(repository, *, limit, concurrency):
        calls.append((repository, limit, concurrency))
        size = batch_sizes.pop(0)
        return [{"source_id": len(calls) * 10 + index} for index in range(size)]

    monkeypatch.setattr(scheduler, "sync_due_sources", fake_sync)
    repository = object()

    results = asyncio.run(scheduler.drain_due_sources(repository, batch_size=3, concurrency=7))

    assert len(results) == 7
    assert calls == [(repository, 3, 7), (repository, 3, 7), (repository, 3, 7)]


def test_drain_due_sources_rejects_unbounded_batch_size():
    try:
        asyncio.run(scheduler.drain_due_sources(object(), batch_size=1001))
    except ValueError as error:
        assert "batch_size" in str(error)
    else:
        raise AssertionError("unbounded scheduler batch size should fail")


def test_due_source_query_supports_the_scheduler_maximum_batch(tmp_path):
    from fortune_intel.storage import JobRepository

    repository = JobRepository(tmp_path / "large-batch.db")
    repository.initialize()
    for index in range(101):
        company_id = repository.upsert_company(f"Company {index}")
        repository.upsert_career_source(
            company_id,
            kind="greenhouse",
            board_token=f"company-{index}",
            base_url=f"https://boards.greenhouse.io/company-{index}",
            policy_approved_at="2026-08-06T00:00:00+00:00",
        )

    assert len(repository.due_career_sources(limit=1000)) == 101


def test_add_source_defaults_to_hourly_refresh():
    args = parser().parse_args(
        [
            "add-source",
            "--company",
            "Example Company",
            "--kind",
            "greenhouse",
            "--board-token",
            "example",
            "--url",
            "https://boards.greenhouse.io/example",
            "--terms-url",
            "https://example.org/terms",
            "--policy-approved-at",
            "2026-08-06T12:00:00Z",
            "--owner-contact",
            "owner@example.org",
        ]
    )

    assert args.interval == 60


def test_reschedule_reviewed_sources_changes_interval_and_makes_them_due(tmp_path):
    repository = JobRepository(tmp_path / "scheduler.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Company")
    source_id = repository.upsert_career_source(
        company_id,
        kind="greenhouse",
        board_token="example",
        base_url="https://boards.greenhouse.io/example",
        sync_interval_minutes=360,
        terms_url="https://example.org/terms",
        policy_approved_at="2026-08-06T12:00:00Z",
        owner_contact="owner@example.org",
    )
    repository.mark_source_finished(source_id, success=True)
    assert repository.due_career_sources() == []

    updated = repository.reschedule_career_sources(sync_interval_minutes=60, company_id=company_id)

    assert updated == 1
    assert repository.due_career_sources()[0]["sync_interval_minutes"] == 60
