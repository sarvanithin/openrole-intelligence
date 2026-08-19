from fortune_intel.domain import SPONSORSHIP_RULE_VERSION, JobRecord
from fortune_intel.services.reassessment import reassess_all_jobs, reassess_company_jobs
from fortune_intel.services.sponsorship import assess_sponsorship
from fortune_intel.storage import JobRepository


def test_reviewed_h1b_link_reassesses_without_changing_observation_time(tmp_path):
    repository = JobRepository(tmp_path / "review.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Brand")
    job_id = repository.upsert_job(
        company_id,
        JobRecord(
            company_name="Example Brand",
            title="Engineer",
            url="https://jobs.example.test/1",
            source="greenhouse:example",
            external_job_id="1",
            description="Build systems.",
        ),
        assess_sponsorship("Build systems."),
    )
    before = repository.get_job(job_id)
    repository.upsert_h1b_employer(
        "Example Legal Employer LLC",
        fiscal_year=2026,
        lca_worker_positions=80,
        source="dol_lca",
        source_url="https://www.dol.gov/agencies/eta/foreign-labor/performance",
        source_document="FY2026.xlsx",
        source_checksum="a" * 64,
        imported_at="2026-08-06T12:00:00+00:00",
    )

    repository.link_reviewed_h1b_employer(
        company_id,
        employer_name="Example Legal Employer LLC",
        fiscal_year=2026,
    )
    assert reassess_company_jobs(repository, company_id) == 1

    after = repository.get_job(job_id)
    assert after["sponsorship_tier"] == "B"
    assert after["last_seen_at"] == before["last_seen_at"]


def test_all_job_reassessment_corrects_tiers_without_changing_observation_time(tmp_path):
    repository = JobRepository(tmp_path / "all.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Brand")
    job_id = repository.upsert_job(
        company_id,
        JobRecord(
            company_name="Example Brand",
            title="Engineer",
            url="https://jobs.example.test/negative",
            source="greenhouse:example",
            external_job_id="negative",
            description="This role is not eligible for visa sponsorship.",
        ),
        assess_sponsorship("Visa sponsorship is available for this position."),
    )
    before = repository.get_job(job_id)

    result = reassess_all_jobs(repository)

    after = repository.get_job(job_id)
    assert result == {"jobs_reassessed": 1, "tier_transitions": {"A->E": 1}}
    assert after["sponsorship_tier"] == "E"
    assert after["sponsorship_rule_version"] == SPONSORSHIP_RULE_VERSION
    assert after["last_seen_at"] == before["last_seen_at"]
