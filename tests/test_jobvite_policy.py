from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_jobvite_board_url,
    probe_jobvite_policy,
)
from fortune_intel.discovery import classify_passive_ats_url


@pytest.mark.parametrize(
    ("url", "company_slug"),
    [
        ("https://jobs.jobvite.com/cronosgroup/", "cronosgroup"),
        ("https://jobs.jobvite.com/firstbank", "firstbank"),
        ("https://jobs.jobvite.com/martinmarietta", "martinmarietta"),
        ("https://jobs.jobvite.com/nrchealth", "nrchealth"),
        ("https://jobs.jobvite.com/nuscale-power", "nuscale-power"),
        ("https://jobs.jobvite.com/loandepot", "loandepot"),
    ],
)
def test_exact_inventoried_board_roots_remain_policy_held(
    url: str,
    company_slug: str,
) -> None:
    candidate = classify_jobvite_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.host == "jobs.jobvite.com"
    assert candidate.company_slug == company_slug
    assert candidate.page_kind == "board"
    assert candidate.job_id == ""
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "third-party redistribution" in candidate.policy_reason


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.jobvite.com/firstbank/job/o9sBzfwv",
        "https://jobs.jobvite.com/aryaka/job/oWZwAfwL?fr=true&nl=1",
    ],
)
def test_first_party_evidenced_job_detail_shape_remains_policy_held(url: str) -> None:
    candidate = classify_jobvite_board_url(url)

    assert candidate is not None
    assert candidate.page_kind == "job_detail"
    assert candidate.job_id
    assert candidate.activation_allowed is False


@pytest.mark.parametrize(
    "url",
    [
        "http://jobs.jobvite.com/acme",
        "https://user@jobs.jobvite.com/acme",
        "https://jobs.jobvite.com:8443/acme",
        "https://jobs.jobvite.com/acme#jobs",
        "https://jobs.jobvite.com.evil.example/acme",
        "https://foo.jobs.jobvite.com/acme",
        "https://careers.jobvite.com/acme",
        "https://jobs.jobvite.com/acme%2Finternal",
        "https://jobs.jobvite.com/../job/123",
        "https://jobs.jobvite.com/sutrobio/apply",
        "https://jobs.jobvite.com/spok/jobAlerts",
        "https://jobs.jobvite.com/acme/job",
        "https://jobs.jobvite.com/acme/job/",
        "https://jobs.jobvite.com/acme?lang=en",
        "https://jobs.jobvite.com/acme/job/123?unknown=1",
        "https://jobs.jobvite.com/acme/job/123?fr=false",
        "https://jobs.jobvite.com/acme/job/123?nl=2",
        "https://jobs.jobvite.com/acme/job/123?nl",
        "https://jobs.jobvite.com/acme/job/123?nl=1&nl=1",
        "https://jobs.jobvite.com/apply",
    ],
)
def test_classifier_rejects_non_job_unsafe_and_lookalike_urls(url: str) -> None:
    assert classify_jobvite_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_jobvite_policy("https://jobs.jobvite.com/firstbank")

    assert result.network_requests == 0
    assert result.endpoint_validated is False
    assert result.complete_manifest_validated is False
    assert result.pagination_validated is False
    assert result.posting_dates_validated is False
    assert result.locations_validated is False
    assert result.us_filter_compatible is False
    assert result.bounded_http_validated is False
    assert result.activation_allowed is False
    assert result.outcome == "policy_held"


def test_passive_inventory_still_recognizes_exact_jobvite_board_url() -> None:
    url = "https://jobs.jobvite.com/firstbank"
    fingerprint = classify_passive_ats_url(url, origin_page="https://firstbank.example/careers")

    assert fingerprint is not None
    assert fingerprint.family == "jobvite"
    assert fingerprint.observed_url == url


@pytest.mark.parametrize("kind", ["jobvite", "jobvite-ats", "employ-jobvite"])
def test_factory_fails_closed_for_policy_held_jobvite(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*third-party redistribution"):
        build_connector(kind, "exact-observed-url")
