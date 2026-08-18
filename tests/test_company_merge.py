from fortune_intel.domain import JobRecord
from fortune_intel.services.sponsorship import assess_sponsorship
from fortune_intel.storage import JobRepository


def test_reviewed_merge_preserves_sources_jobs_versions_and_future_upserts(tmp_path):
    repository = JobRepository(tmp_path / "merge.db")
    repository.initialize()
    source_id = repository.upsert_company("Example", ats_type="greenhouse")
    target_id = repository.upsert_company("EXAMPLE INC", sec_cik="123", ticker="EXMP")
    repository.upsert_career_source(
        source_id,
        kind="greenhouse",
        board_token="example",
        base_url="https://boards.greenhouse.io/example",
        policy_approved_at="2026-08-06T12:00:00+00:00",
    )
    record = JobRecord(
        company_name="Example",
        title="Engineer",
        url="https://boards.greenhouse.io/example/jobs/1",
        source="greenhouse:example",
        external_job_id="1",
        description="Build systems",
    )
    repository.upsert_job(source_id, record, assess_sponsorship(record.description))

    repository.merge_companies(
        source_id,
        target_id,
        actor="reviewer@example.org",
        reason="Official SEC identity reviewed",
    )

    assert repository.find_company_by_normalized_name("Example") is None
    target = repository.find_company_by_normalized_name("EXAMPLE INC")
    assert target["sec_cik"] == "0000000123"
    assert repository.source_status()[0]["company_name"] == "EXAMPLE INC"
    jobs = repository.list_jobs(company="example-inc")
    assert len(jobs) == 1
    first_seen = jobs[0]["first_seen_at"]

    updated = JobRecord(
        company_name="EXAMPLE INC",
        title="Engineer",
        url=record.url,
        source=record.source,
        external_job_id=record.external_job_id,
        description="Build reliable systems",
    )
    repository.upsert_job(target_id, updated, assess_sponsorship(updated.description))
    assert repository.list_jobs(company="example-inc")[0]["first_seen_at"] == first_seen
    assert "Reviewed identity merge" in repository.company_coverage_events(target_id)[0]["reason"]


def test_merge_rejects_conflicting_sec_identities(tmp_path):
    repository = JobRepository(tmp_path / "conflict.db")
    repository.initialize()
    source_id = repository.upsert_company("First", sec_cik="1")
    target_id = repository.upsert_company("Second", sec_cik="2")

    try:
        repository.merge_companies(
            source_id,
            target_id,
            actor="reviewer@example.org",
            reason="Should fail",
        )
    except ValueError as error:
        assert "conflicting SEC" in str(error)
    else:
        raise AssertionError("conflicting SEC identities must not merge")
