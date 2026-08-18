from fortune_intel.domain import JobRecord
from fortune_intel.services.ingestion import CompanySource
from fortune_intel.services.sponsorship import EmployerHistory, assess_sponsorship
from fortune_intel.storage import JobRepository


def make_job(title="Data Engineer", external_id="job-1"):
    return JobRecord(
        company_name="Example Labs",
        title=title,
        url=f"https://jobs.example.com/{external_id}",
        source="greenhouse",
        external_job_id=external_id,
        location="New York, NY",
        description="Build pipelines.",
    )


def test_title_edit_updates_stable_job_in_place(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Labs", ats_type="greenhouse")
    assessment = assess_sponsorship(
        "",
        EmployerHistory(
            lca_worker_positions=20, latest_fiscal_year=2026, entity_match_confidence=1
        ),
        current_year=2026,
    )
    first_id = repository.upsert_job(company_id, make_job(), assessment)
    second_id = repository.upsert_job(company_id, make_job("Senior Data Engineer"), assessment)

    assert first_id == second_id
    jobs = repository.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Data Engineer"


def test_location_edit_reclassifies_stable_job_without_resetting_first_seen(tmp_path):
    repository = JobRepository(tmp_path / "geography.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Labs")
    assessment = assess_sponsorship("")
    canadian = JobRecord(**{**make_job().__dict__, "location": "Toronto, Ontario, Canada"})
    job_id = repository.upsert_job(company_id, canadian, assessment)
    first_seen = repository.get_job(job_id)["first_seen_at"]

    us_job = JobRecord(**{**canadian.__dict__, "location": "Austin, TX"})
    second_id = repository.upsert_job(company_id, us_job, assessment)

    assert second_id == job_id
    assert repository.get_job(job_id)["first_seen_at"] == first_seen
    assert repository.count_jobs(us_eligibility="eligible") == 1
    assert repository.count_jobs(us_eligibility="ineligible") == 0


def test_job_closes_only_after_two_non_empty_complete_manifests(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Labs")
    assessment = assess_sponsorship("")
    repository.upsert_job(company_id, make_job("Data Engineer", "job-1"), assessment)
    repository.upsert_job(company_id, make_job("ML Engineer", "job-2"), assessment)

    assert repository.finalize_complete_manifest(company_id, "greenhouse", ["job-2"]) == 0
    assert len(repository.list_jobs()) == 2
    assert repository.finalize_complete_manifest(company_id, "greenhouse", ["job-2"]) == 1
    assert [job["title"] for job in repository.list_jobs()] == ["ML Engineer"]


def test_empty_manifest_cannot_mass_close_jobs(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Labs")
    repository.upsert_job(company_id, make_job(), assess_sponsorship(""))

    repository.finalize_complete_manifest(company_id, "greenhouse", [])
    repository.finalize_complete_manifest(company_id, "greenhouse", [])
    assert len(repository.list_jobs()) == 1


def test_manifest_observation_counters_advance_preserve_and_reset(tmp_path):
    repository = JobRepository(tmp_path / "observations.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Labs")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/example",
        kind="greenhouse",
        confidence=1,
        evidence={},
    )

    assert (
        repository.record_candidate_manifest_observation(candidate_id, complete=True, jobs_seen=0)
        == 1
    )
    assert (
        repository.record_candidate_manifest_observation(candidate_id, complete=False, jobs_seen=0)
        == 1
    )
    assert (
        repository.record_candidate_manifest_observation(candidate_id, complete=True, jobs_seen=3)
        == 0
    )


def test_company_source_rejects_private_network_url():
    try:
        CompanySource("Unsafe", "http://127.0.0.1/admin")
    except ValueError as error:
        assert "private or local" in str(error)
    else:
        raise AssertionError("private source URL should be rejected")


def test_company_source_rejects_localhost_hostname():
    try:
        CompanySource("Unsafe", "http://localhost/admin")
    except ValueError as error:
        assert "private or local" in str(error)
    else:
        raise AssertionError("localhost should be rejected")


def test_company_identity_does_not_merge_legal_suffixes_or_unicode(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    identifiers = {
        repository.upsert_company("Acme Inc"),
        repository.upsert_company("Acme LLC"),
        repository.upsert_company("東京技術"),
        repository.upsert_company("서울기술"),
    }
    assert len(identifiers) == 4


def test_source_case_is_normalized_before_identity_insert(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Labs")
    job = make_job()
    upper_source = JobRecord(**{**job.__dict__, "source": "Workday"})
    lower_source = JobRecord(
        **{**job.__dict__, "source": "workday", "title": "Senior Data Engineer"}
    )
    first = repository.upsert_job(company_id, upper_source, assess_sponsorship(""))
    second = repository.upsert_job(company_id, lower_source, assess_sponsorship(""))
    assert first == second
    assert repository.list_jobs()[0]["title"] == "Senior Data Engineer"


def test_jobs_order_by_source_opening_date_with_first_seen_fallback(tmp_path):
    repository = JobRepository(tmp_path / "dates.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Labs")
    assessment = assess_sponsorship("")
    jobs = (
        JobRecord(
            **{
                **make_job("Older source date", "older").__dict__,
                "source_opened_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        JobRecord(
            **{
                **make_job("Newest source date", "newest").__dict__,
                "source_opened_at": "2026-07-01T00:00:00+00:00",
            }
        ),
        make_job("No source date", "fallback"),
    )
    identifiers = [repository.upsert_job(company_id, job, assessment) for job in jobs]
    with repository.connect() as connection:
        connection.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE id = ?",
            ("2026-06-01T00:00:00+00:00", identifiers[2]),
        )

    listed = repository.list_jobs()

    assert [job["title"] for job in listed] == [
        "Newest source date",
        "No source date",
        "Older source date",
    ]
    fallback = listed[1]
    assert fallback["source_opened_at"] is None
    assert fallback["display_date"] == "2026-06-01T00:00:00+00:00"
    assert fallback["date_provenance"] == "first_seen_at"
