from __future__ import annotations

import json

from fortune_intel.services.audited_redirect_promotion import promote_audited_redirects
from fortune_intel.services.licensed_lead_verification import LeadPage
from fortune_intel.storage import JobRepository


PUBLIC_IP = "93.184.216.34"


def _write_redirect(path, *, company_id, source_url, location, target_type="registry_portal"):
    value = {
        "key": f"{target_type}:{company_id}:{source_url}",
        "company_id": company_id,
        "company_name": "Example Industries, Inc.",
        "url": source_url,
        "family": "unknown_external",
        "target_type": target_type,
        "outcome": "redirect",
        "http_status": 302,
        "location": location,
        "started_at": "2026-08-15T00:00:00+00:00",
        "completed_at": "2026-08-15T00:00:01+00:00",
    }
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _registry_origin(repository, company_id, source_url):
    repository.upsert_source_fingerprint(
        company_id,
        observed_url=source_url,
        family="unknown_external",
        evidence={
            "review_method": "user_supplied_career_url_registry",
            "verification_status": "unverified",
            "activation_allowed": False,
        },
        actor="registry",
        mark_discovered=False,
    )


def test_promotes_only_current_audited_registry_redirect_after_direct_identity_check(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    source_url = "https://company.example/careers"
    _registry_origin(repository, company_id, source_url)
    audit_path = tmp_path / "portal-results.jsonl"
    _write_redirect(
        audit_path,
        company_id=company_id,
        source_url=source_url,
        location="https://boards.greenhouse.io/exampleindustries",
    )

    report = promote_audited_redirects(
        repository,
        audit_results_path=audit_path,
        actor="test",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T00:00:00+00:00",
        resolver=lambda _: [PUBLIC_IP],
        page_fetcher=lambda _: LeadPage(
            200,
            "https://boards.greenhouse.io/exampleindustries",
            "text/html",
            b"<title>Example Industries careers</title>",
        ),
    )

    assert report == {"scanned": 1, "verified": 1, "rejected": 0, "skipped": 0}
    candidate = repository.list_source_candidates(company_id)[0]
    assert candidate["status"] == "discovered"
    assert candidate["kind"] == "greenhouse"
    assert candidate["terms_status"] == "permitted"
    assert candidate["evidence"]["review_method"] == "audited_redirect_exact_ats_identity"
    assert candidate["evidence"]["audit_provenance"]["source_url"] == source_url
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM career_sources").fetchone()[0] == 0


def test_never_trusts_a_redirect_audit_record_without_a_current_first_party_origin(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    audit_path = tmp_path / "portal-results.jsonl"
    _write_redirect(
        audit_path,
        company_id=company_id,
        source_url="https://third-party.example/company/example",
        location="https://boards.greenhouse.io/exampleindustries",
    )

    report = promote_audited_redirects(
        repository,
        audit_results_path=audit_path,
        actor="test",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T00:00:00+00:00",
        resolver=lambda _: [PUBLIC_IP],
        page_fetcher=lambda _: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    assert report == {"scanned": 1, "verified": 0, "rejected": 0, "skipped": 1}
    assert repository.list_source_candidates(company_id) == []


def test_rejects_private_supported_ats_destination_before_fetching_it(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    source_url = "https://company.example/careers"
    _registry_origin(repository, company_id, source_url)
    audit_path = tmp_path / "portal-results.jsonl"
    _write_redirect(
        audit_path,
        company_id=company_id,
        source_url=source_url,
        location="https://boards.greenhouse.io/exampleindustries",
    )

    report = promote_audited_redirects(
        repository,
        audit_results_path=audit_path,
        actor="test",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T00:00:00+00:00",
        resolver=lambda _: ["10.0.0.7"],
        page_fetcher=lambda _: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    assert report == {"scanned": 1, "verified": 0, "rejected": 0, "skipped": 1}
