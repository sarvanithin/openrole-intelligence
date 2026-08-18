import pytest

from fortune_intel.storage import JobRepository


def test_company_identity_and_default_coverage_are_persisted(tmp_path):
    repository = JobRepository(tmp_path / "coverage.db")
    repository.initialize()
    company_id = repository.upsert_company(
        "Example Incorporated",
        sec_cik="0000123456",
        ticker="exmp",
        website_url="https://www.example.com",
    )

    company = repository.find_company_by_normalized_name("Example Incorporated")
    assert company["sec_cik"] == "0000123456"
    assert company["ticker"] == "EXMP"
    assert company["website_url"] == "https://www.example.com/"
    assert repository.get_company_coverage(company_id)["disposition"] == "unreviewed"

    same_company_id = repository.upsert_company("Example Incorporated", sec_cik=123456)
    assert same_company_id == company_id
    assert repository.find_company_by_normalized_name("Example Incorporated")["sec_cik"] == (
        "0000123456"
    )


def test_candidate_evidence_policy_reviews_and_dispositions_are_auditable(tmp_path):
    repository = JobRepository(tmp_path / "audit.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://jobs.example.com/openings/?utm_source=directory",
        kind="workday",
        confidence=0.91,
        evidence={"method": "official-site-link", "redirects": 1},
        robots_status="allowed",
        robots_checked_at="2026-08-06T12:00:00+00:00",
        terms_url="https://example.com/terms",
        terms_status="review_required",
        terms_reviewed_at="2026-08-06T12:05:00+00:00",
        discovered_at="2026-08-06T11:55:00+00:00",
    )
    repository.review_source_candidate(
        candidate_id,
        status="approved",
        reviewed_by="operator@example.com",
        review_notes="Official corporate careers link and policy reviewed.",
        reviewed_at="2026-08-06T12:10:00+00:00",
    )
    repository.set_company_disposition(
        company_id,
        "approved",
        reason="Source approved; connector implementation pending",
        actor="operator@example.com",
        reviewed_at="2026-08-06T12:11:00+00:00",
        stale_after="2026-09-05T12:11:00+00:00",
        candidate_id=candidate_id,
    )

    candidate = repository.list_source_candidates(company_id)[0]
    coverage = repository.get_company_coverage(company_id)
    event = repository.company_coverage_events(company_id)[0]
    assert candidate["candidate_url"] == "https://jobs.example.com/openings"
    assert candidate["evidence"] == {"method": "official-site-link", "redirects": 1}
    assert candidate["robots_status"] == "allowed"
    assert candidate["terms_status"] == "review_required"
    assert candidate["status"] == "approved"
    assert coverage["disposition"] == "approved"
    assert coverage["last_discovered_at"] == "2026-08-06T11:55:00+00:00"
    assert event["from_disposition"] == "unreviewed"
    assert event["to_disposition"] == "approved"
    assert event["candidate_id"] == candidate_id


def test_coverage_rejects_unsafe_or_unaudited_inputs(tmp_path):
    repository = JobRepository(tmp_path / "validation.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")

    with pytest.raises(ValueError, match="public HTTP"):
        repository.upsert_source_candidate(
            company_id,
            candidate_url="http://127.0.0.1/jobs",
            kind="custom",
            confidence=0.5,
            evidence={},
        )
    with pytest.raises(ValueError, match="timezone"):
        repository.upsert_source_candidate(
            company_id,
            candidate_url="https://jobs.example.com",
            kind="custom",
            confidence=0.5,
            evidence={},
            robots_checked_at="2026-08-06T12:00:00",
        )
    with pytest.raises(ValueError, match="actor is required"):
        repository.set_company_disposition(
            company_id, "no_source", reason="No careers page found", actor=""
        )


def test_rediscovery_refreshes_evidence_without_erasing_policy_approval(tmp_path):
    repository = JobRepository(tmp_path / "rediscovery.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://jobs.example.com/openings",
        kind="workday",
        confidence=0.9,
        evidence={"crawl": 1},
        robots_status="allowed",
        terms_status="review_required",
    )
    repository.review_source_candidate(
        candidate_id,
        status="approved",
        reviewed_by="operator@example.com",
        review_notes="Complete manifest verified",
        reviewed_at="2026-08-06T12:10:00+00:00",
        terms_url="https://example.com/terms",
        terms_status="permitted",
    )

    repeated_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://jobs.example.com/openings",
        kind="workday",
        confidence=0.99,
        evidence={"crawl": 2},
        robots_status="allowed",
        terms_status="review_required",
        discovered_at="2026-08-07T12:00:00+00:00",
    )

    stored = repository.get_source_candidate(candidate_id)
    assert repeated_id == candidate_id
    assert stored["status"] == "approved"
    assert stored["terms_status"] == "permitted"
    assert stored["terms_url"] == "https://example.com/terms"
    assert stored["terms_reviewed_at"] == "2026-08-06T12:10:00+00:00"
    assert stored["evidence"] == {"crawl": 2}
    assert stored["confidence"] == 0.99


def test_initialize_repairs_a_legacy_discovery_policy_downgrade(tmp_path):
    repository = JobRepository(tmp_path / "repair.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/example",
        kind="greenhouse",
        confidence=0.99,
        evidence={"method": "official-site-link"},
        robots_status="allowed",
        terms_status="review_required",
    )
    repository.review_source_candidate(
        candidate_id,
        status="approved",
        reviewed_by="operator@example.com",
        reviewed_at="2026-08-06T12:10:00+00:00",
    )
    repository.upsert_career_source(
        company_id,
        kind="greenhouse",
        board_token="example",
        base_url="https://boards.greenhouse.io/example",
        terms_url="https://example.com/terms",
        policy_approved_at="2026-08-06T12:10:00+00:00",
        owner_contact="operator@example.com",
    )

    repository.initialize()

    stored = repository.get_source_candidate(candidate_id)
    assert stored["terms_status"] == "permitted"
    assert stored["terms_url"] == "https://example.com/terms"
    assert stored["terms_reviewed_at"] == "2026-08-06T12:10:00+00:00"
