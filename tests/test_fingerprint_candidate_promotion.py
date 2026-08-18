from __future__ import annotations

from fortune_intel.services.fingerprint_candidate_promotion import (
    promote_verified_seed_fingerprints,
)
from fortune_intel.cli import parser
from fortune_intel.storage import JobRepository


def _verified_company(repository: JobRepository) -> int:
    company_id = repository.upsert_company("Example Industries, Inc.", website_url="https://example.com/")
    repository.set_company_disposition(
        company_id,
        "unreviewed",
        reason="Canonical website seed verified from https://example.com/",
        actor="test",
        reviewed_at="2026-08-14T00:00:00+00:00",
    )
    return company_id


def test_promotes_exact_supported_fingerprint_with_verified_seed(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = _verified_company(repository)
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://example.wd5.myworkdayjobs.com/careers",
        family="unknown_external",
        evidence={
            "origin_page": "https://example.com/careers",
            "seed_urls_checked": ["https://example.com/"],
            "fingerprint_evidence": ["exact outbound URL"],
        },
        actor="test",
    )

    assert promote_verified_seed_fingerprints(repository, actor="test") == {
        "scanned": 1,
        "promoted": 1,
        "not_supported": 0,
        "not_primary": 0,
    }
    candidate = repository.list_source_candidates(company_id)[0]
    assert candidate["kind"] == "workday"
    assert candidate["evidence"]["review_method"] == "verified_seed_fingerprint_promotion"


def test_never_promotes_third_party_lead(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = _verified_company(repository)
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://example.wd5.myworkdayjobs.com/careers",
        family="unknown_external",
        evidence={
            "review_method": "third_party_discovery_lead",
            "activation_allowed": False,
            "seed_urls_checked": ["https://example.com/"],
        },
        actor="test",
    )

    report = promote_verified_seed_fingerprints(repository, actor="test")
    assert report["not_primary"] == 1
    assert repository.list_source_candidates(company_id) == []


def test_cli_parses_verified_seed_promotion_command():
    args = parser().parse_args(
        ["promote-verified-seed-fingerprints", "--actor", "scheduler", "--limit", "10"]
    )
    assert args.command == "promote-verified-seed-fingerprints"
    assert args.limit == 10
