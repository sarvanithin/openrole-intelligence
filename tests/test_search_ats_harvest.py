from __future__ import annotations

import json

import pytest

from fortune_intel.services.licensed_lead_verification import LeadPage
from fortune_intel.services.search_ats_harvest import (
    harvest_verified_search_ats_results,
    load_recorded_search_results,
    recorded_search_result,
)
from fortune_intel.storage import JobRepository


def _result(company_id: int, company_name: str, *, url: str = "https://boards.greenhouse.io/example"):
    return recorded_search_result(
        {
            "company_id": company_id,
            "company_name": company_name,
            "provider": "licensed-search-export",
            "query": f"{company_name} careers",
            "result_url": url,
            "retrieved_at": "2026-08-15T03:00:00+00:00",
            "rank": 1,
        },
        line=1,
    )


def _page(url: str) -> LeadPage:
    return LeadPage(200, url, "text/html", b"<title>Example Industries careers</title>")


def test_harvest_retains_only_directly_verified_primary_ats_result(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")

    report = harvest_verified_search_ats_results(
        repository,
        [_result(company_id, "Example Industries, Inc.")],
        actor="search-harvester",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T03:00:00+00:00",
        page_fetcher=_page,
    )

    assert report == {
        "input": 1,
        "missing_career_url": 1,
        "not_supported_ats": 0,
        "redirected": 0,
        "identity_rejected": 0,
        "verified_candidates": 1,
        "skipped_existing_career_artifact": 0,
    }
    candidate = repository.list_source_candidates(company_id)[0]
    assert candidate["status"] == "discovered"
    assert candidate["evidence"]["review_method"] == "recorded_search_result_direct_primary_ats_identity"
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM career_sources").fetchone()["n"] == 0
        assert (
            connection.execute("SELECT COUNT(*) AS n FROM career_source_fingerprints").fetchone()["n"]
            == 0
        )


def test_harvest_never_retains_nonmatching_or_redirected_search_result(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    result = _result(company_id, "Example Industries, Inc.")

    rejected = harvest_verified_search_ats_results(
        repository,
        [result],
        actor="search-harvester",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T03:00:00+00:00",
        page_fetcher=lambda url: LeadPage(200, url, "text/html", b"<title>Different Employer</title>"),
    )
    assert rejected["identity_rejected"] == 1
    assert repository.list_source_candidates(company_id) == []

    redirected = harvest_verified_search_ats_results(
        repository,
        [result],
        actor="search-harvester",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T03:00:00+00:00",
        page_fetcher=lambda _: LeadPage(302, "https://elsewhere.example/", "text/html", b""),
    )
    assert redirected["redirected"] == 1
    assert repository.list_source_candidates(company_id) == []


def test_harvest_skips_company_that_already_has_a_career_artifact(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://jobs.lever.co/example",
        family="unknown_external",
        evidence={"review_method": "user_supplied_career_url_registry"},
        actor="registry",
        mark_discovered=False,
    )
    report = harvest_verified_search_ats_results(
        repository,
        [_result(company_id, "Example Industries, Inc.")],
        actor="search-harvester",
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-15T03:00:00+00:00",
        page_fetcher=_page,
    )
    assert report["skipped_existing_career_artifact"] == 1
    assert repository.list_source_candidates(company_id) == []


def test_jsonl_requires_provenance_and_validates_entire_file(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text(
        json.dumps(
            {
                "company_id": 1,
                "company_name": "Example Industries, Inc.",
                "provider": "licensed-search-export",
                "query": "Example Industries careers",
                "result_url": "https://boards.greenhouse.io/example",
                "retrieved_at": "2026-08-15T03:00:00+00:00",
            }
        )
        + "\n"
        + json.dumps({"company_id": 2})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 2: company_name is required"):
        load_recorded_search_results(path)
