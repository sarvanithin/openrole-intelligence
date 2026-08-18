from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from fortune_intel.api import create_app
from fortune_intel.config import Settings
from fortune_intel.domain import JobRecord
from fortune_intel.services.sponsorship import assess_sponsorship
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_audit_ops import coverage_audit_summary


def build_ingested_company(
    repository,
    *,
    name="Exact Company",
    sec_cik="0000123456",
    location="Austin, TX",
):
    career_url = "https://boards.greenhouse.io/exact-company"
    company_id = repository.upsert_company(name, sec_cik=sec_cik, career_url=career_url)
    repository.set_company_disposition(
        company_id,
        "unreviewed",
        reason=(
            f"Canonical company URL imported by exact SEC CIK {sec_cik}: "
            "Wikidata https://www.wikidata.org/entity/Q42 P5531 -> "
            f"P10311 ({career_url})"
        ),
        actor="identity-reviewer@example.test",
    )
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url=career_url,
        kind="greenhouse",
        confidence=1.0,
        evidence={"method": "official-career-url", "pages_checked": [career_url]},
        robots_status="allowed",
        terms_status="permitted",
    )
    repository.review_source_candidate(
        candidate_id,
        status="approved",
        reviewed_by="source-reviewer@example.test",
        review_notes="Connector probe returned 2 jobs in a complete manifest",
        terms_status="permitted",
    )
    source_id = repository.upsert_career_source(
        company_id,
        kind="greenhouse",
        board_token="exact-company",
        base_url=career_url,
        sync_interval_minutes=60,
        terms_url="https://www.greenhouse.com/terms",
        policy_approved_at=datetime.now(UTC).isoformat(),
        owner_contact="source-reviewer@example.test",
    )
    run_id = repository.start_sync_run("greenhouse:exact-company", company_id)
    repository.upsert_job(
        company_id,
        JobRecord(
            company_name=name,
            title="Platform Engineer",
            url="https://boards.greenhouse.io/exact-company/jobs/1",
            source="greenhouse:exact-company",
            external_job_id="1",
            location=location,
            source_opened_at="2026-08-07T10:00:00+00:00",
        ),
        assess_sponsorship(""),
    )
    repository.finish_sync_run(run_id, status="success", complete=True, jobs_seen=1)
    repository.mark_source_finished(source_id, success=True)
    return company_id


def test_strict_audit_passes_only_after_every_gate_is_evidenced(tmp_path):
    repository = JobRepository(tmp_path / "audit.db")
    repository.initialize()
    build_ingested_company(repository)

    record = repository.company_coverage_audit()[0]

    assert record["covered"] is True
    assert record["completed_gates"] == record["total_gates"] == 7
    assert record["next_action"] == "complete"
    assert record["first_seen_fallback_jobs"] == 0
    assert coverage_audit_summary([record])["covered"] == 1


def test_successful_jobs_never_make_an_unverified_identity_covered(tmp_path):
    repository = JobRepository(tmp_path / "unverified.db")
    repository.initialize()
    build_ingested_company(repository, name="No Exact Identity", sec_cik="")

    record = repository.company_coverage_audit()[0]

    assert record["successful_platform_ingestion"] is True
    assert record["identity_verified"] is False
    assert record["portal_seed_verified"] is False
    assert record["covered"] is False
    assert record["next_action"] == "verify_exact_company_identity"


def test_successful_source_with_zero_us_roles_is_not_an_ingestion_failure(tmp_path):
    repository = JobRepository(tmp_path / "non-us.db")
    repository.initialize()
    build_ingested_company(repository, location="Toronto, Ontario, Canada")

    record = repository.company_coverage_audit()[0]

    assert record["successful_platform_ingestion"] is True
    assert record["active_jobs"] == 0
    assert record["opening_date_provenance_complete"] is True


def test_first_seen_date_fallback_fails_opening_date_gate(tmp_path):
    repository = JobRepository(tmp_path / "dates.db")
    repository.initialize()
    company_id = build_ingested_company(repository)
    repository.upsert_job(
        company_id,
        JobRecord(
            company_name="Exact Company",
            title="Observed Only Role",
            url="https://boards.greenhouse.io/exact-company/jobs/2",
            source="greenhouse:exact-company",
            external_job_id="2",
            location="Remote — United States",
        ),
        assess_sponsorship(""),
    )

    record = repository.company_coverage_audit()[0]

    assert record["opening_date_provenance_complete"] is False
    assert record["first_seen_fallback_jobs"] == 1
    assert record["covered"] is False
    assert record["next_action"] == "resolve_opening_date_provenance"


def test_source_outside_cadence_fails_freshness_gate(tmp_path):
    repository = JobRepository(tmp_path / "stale.db")
    repository.initialize()
    build_ingested_company(repository)

    record = repository.company_coverage_audit(as_of=datetime.now(UTC) + timedelta(minutes=121))[0]

    assert record["fresh"] is False
    assert record["stale_sources"] == 1
    assert record["covered"] is False
    assert record["next_action"] == "restore_source_freshness"


def test_public_coverage_checklist_is_paginated_and_does_not_expose_urls(tmp_path):
    settings = Settings(
        database_path=tmp_path / "api.db",
        environment="test",
        allowed_hosts=("testserver",),
        public_base_url="https://jobs.example.test",
    )
    app = create_app(settings=settings)
    build_ingested_company(app.state.repository)
    app.state.repository.upsert_company("Still Incomplete", sec_cik="0000654321")

    with TestClient(app) as client:
        payload = client.get(
            "/api/coverage/companies", params={"status": "incomplete", "limit": 1}
        ).json()

    assert payload["total"] == 1
    assert payload["items"][0]["company_name"] == "Still Incomplete"
    assert payload["items"][0]["covered"] is False
    assert "portal_seed_url" not in payload["items"][0]
    assert "base_url" not in payload["items"][0]
    assert payload["definition"]["covered"].startswith("All seven gates")
