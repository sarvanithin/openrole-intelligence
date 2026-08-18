from __future__ import annotations

from fortune_intel.services.licensed_lead_verification import LeadPage, promote_verified_discovery_leads
from fortune_intel.storage import JobRepository


def test_verified_origin_promotes_only_exact_company_identity(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://boards.greenhouse.io/exampleindustries",
        family="unknown_external",
        evidence={
            "review_method": "third_party_discovery_lead",
            "verification_status": "unverified",
            "activation_allowed": False,
        },
        actor="codex-openjobs-license-review@local",
        mark_discovered=False,
    )

    report = promote_verified_discovery_leads(
        repository,
        actor="test",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-13T02:00:00+00:00",
        page_fetcher=lambda _: LeadPage(
            200,
            "https://boards.greenhouse.io/exampleindustries",
            "text/html",
            b"<title>Example Industries careers</title>",
        ),
    )

    assert report == {"scanned": 1, "verified": 1, "rejected": 0, "skipped": 0}
    candidate = repository.list_source_candidates(company_id)[0]
    assert candidate["kind"] == "greenhouse"
    assert candidate["evidence"]["review_method"] == "primary_source_exact_ats_url"
    with repository.connect() as connection:
        lead = connection.execute("SELECT evidence_json FROM career_source_fingerprints").fetchone()
    assert '"verification_status":"verified"' in str(lead["evidence_json"])


def test_nonmatching_or_redirected_lead_never_becomes_candidate(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://boards.greenhouse.io/exampleindustries",
        family="unknown_external",
        evidence={
            "review_method": "third_party_discovery_lead",
            "verification_status": "unverified",
            "activation_allowed": False,
        },
        actor="codex-openjobs-license-review@local",
        mark_discovered=False,
    )

    report = promote_verified_discovery_leads(
        repository,
        actor="test",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-13T02:00:00+00:00",
        page_fetcher=lambda _: LeadPage(
            200,
            "https://boards.greenhouse.io/exampleindustries",
            "text/html",
            b"<title>Another Employer careers</title>",
        ),
    )

    assert report["rejected"] == 1
    assert repository.list_source_candidates(company_id) == []


def test_user_registry_lead_uses_the_same_direct_identity_gate(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://boards.greenhouse.io/exampleindustries",
        family="unknown_external",
        evidence={
            "review_method": "user_supplied_career_url_registry",
            "verification_status": "unverified",
            "activation_allowed": False,
        },
        actor="user-registry",
        mark_discovered=False,
    )

    report = promote_verified_discovery_leads(
        repository,
        actor="test",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T00:00:00+00:00",
        page_fetcher=lambda _: LeadPage(
            200,
            "https://boards.greenhouse.io/exampleindustries",
            "text/html",
            b"<title>Example Industries careers</title>",
        ),
    )

    assert report == {"scanned": 1, "verified": 1, "rejected": 0, "skipped": 0}
    assert repository.list_source_candidates(company_id)[0]["kind"] == "greenhouse"
    with repository.connect() as connection:
        lead = connection.execute("SELECT evidence_json FROM career_source_fingerprints").fetchone()
    assert '"verification_status":"verified"' in str(lead["evidence_json"])
