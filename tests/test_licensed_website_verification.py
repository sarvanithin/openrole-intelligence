from __future__ import annotations

from fortune_intel.services.licensed_website_verification import (
    WebsitePage,
    promote_verified_website_leads,
)
from fortune_intel.storage import JobRepository


def _lead(repository: JobRepository, company_id: int, *, url: str = "https://example.com/about"):
    repository.upsert_source_fingerprint(
        company_id,
        observed_url=url,
        family="unknown_external",
        evidence={
            "review_method": "licensed_company_website_lead",
            "verification_status": "unverified",
            "website_seed_promotion_allowed": False,
            "primary_site_identity_confirmation_required": True,
        },
        actor="licensed-dataset-reviewer",
        mark_discovered=False,
    )


def test_first_party_json_ld_exact_identity_promotes_only_a_website_seed(tmp_path):
    repository = JobRepository(tmp_path / "website-verification.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    _lead(repository, company_id)

    report = promote_verified_website_leads(
        repository,
        actor="website-verifier",
        page_fetcher=lambda _: WebsitePage(
            200,
            "https://example.com/about",
            "text/html; charset=utf-8",
            b'''<script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Organization","legalName":"Example Industries, Inc."}
            </script>''',
        ),
    )

    assert report == {"scanned": 1, "verified": 1, "rejected": 0, "skipped": 0}
    company = repository.find_company_by_normalized_name("Example Industries, Inc.")
    assert company["website_url"] == "https://example.com/about"
    assert repository.list_source_candidates(company_id) == []
    assert repository.source_status() == []
    fingerprint = repository.list_source_fingerprints(company_id)[0]
    details = fingerprint["evidence"]["website_seed_verification_attempt"]
    assert fingerprint["evidence"]["verification_status"] == "verified"
    assert details["identity_declaration"] == "json_ld:legalName"
    assert len(details["body_sha256"]) == 64


def test_title_or_page_text_alone_is_not_first_party_identity_confirmation(tmp_path):
    repository = JobRepository(tmp_path / "website-verification.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    _lead(repository, company_id)

    report = promote_verified_website_leads(
        repository,
        actor="website-verifier",
        page_fetcher=lambda _: WebsitePage(
            200,
            "https://example.com/about",
            "text/html",
            b"<title>Example Industries, Inc.</title><p>Example Industries, Inc.</p>",
        ),
    )

    assert report["rejected"] == 1
    assert repository.find_company_by_normalized_name("Example Industries, Inc.")["website_url"] == ""
    assert repository.list_source_candidates(company_id) == []


def test_rejects_profile_host_even_when_it_declares_the_company_name(tmp_path):
    repository = JobRepository(tmp_path / "website-verification.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    _lead(repository, company_id, url="https://www.linkedin.com/company/example-industries")

    report = promote_verified_website_leads(
        repository,
        actor="website-verifier",
        page_fetcher=lambda _: WebsitePage(
            200,
            "https://www.linkedin.com/company/example-industries",
            "text/html",
            b'<meta property="og:site_name" content="Example Industries, Inc.">',
        ),
    )

    assert report == {"scanned": 1, "verified": 0, "rejected": 1, "skipped": 0}
    assert repository.find_company_by_normalized_name("Example Industries, Inc.")["website_url"] == ""


def test_redirected_or_existing_website_lead_is_not_promoted_or_overwritten(tmp_path):
    repository = JobRepository(tmp_path / "website-verification.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    _lead(repository, company_id)

    report = promote_verified_website_leads(
        repository,
        actor="website-verifier",
        page_fetcher=lambda _: WebsitePage(301, "https://other.example/", "text/html", b""),
    )
    assert report["rejected"] == 1
    assert repository.find_company_by_normalized_name("Example Industries, Inc.")["website_url"] == ""

    existing_id = repository.upsert_company("Already Seeded", website_url="https://known.example/")
    _lead(repository, existing_id, url="https://lead.example/")
    report = promote_verified_website_leads(repository, actor="website-verifier")
    assert report == {"scanned": 0, "verified": 0, "rejected": 0, "skipped": 0}
    assert repository.find_company_by_normalized_name("Already Seeded")["website_url"] == "https://known.example/"
