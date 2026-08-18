from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_paylocity_board_url,
    probe_paylocity_policy,
)


@pytest.mark.parametrize(
    ("url", "family", "kind", "identifier"),
    [
        (
            "https://recruiting.paylocity.com/Recruiting/Jobs/All/"
            "20c9b648-b2ff-4d0b-a61b-562f1bb835e1/Avidia-Bank",
            "uuid_board",
            "board",
            "20c9b648-b2ff-4d0b-a61b-562f1bb835e1",
        ),
        (
            "https://recruiting.paylocity.com/Recruiting/Jobs/All/"
            "820aea30-f136-438f-b364-05da98576c72",
            "uuid_board",
            "board",
            "820aea30-f136-438f-b364-05da98576c72",
        ),
        (
            "https://recruiting.paylocity.com/Recruiting/Jobs/Details/3739507",
            "job",
            "job_detail",
            "3739507",
        ),
        (
            "https://recruiting.paylocity.com/recruiting/jobs/Details/4180907/"
            "BULLFROG-AI-MANAGEMENT-LLC/Staff-Platform-Engineer",
            "job",
            "job_detail",
            "4180907",
        ),
        (
            "https://recruiting.paylocity.com/recruiting/jobs/Apply/4208842/"
            "Sionna-Therapeutics-Inc/Director-CMC-Project-Management",
            "job",
            "application",
            "4208842",
        ),
        (
            "https://recruiting.paylocity.com/recruiting/jobs/List/3961/Somerset-Savings-Bank-SLA",
            "legacy_list",
            "board",
            "3961",
        ),
    ],
)
def test_exact_inventoried_paylocity_shapes_remain_policy_held(
    url: str,
    family: str,
    kind: str,
    identifier: str,
) -> None:
    candidate = classify_paylocity_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.portal_family == family
    assert candidate.observation_kind == kind
    assert (candidate.board_identifier or candidate.job_identifier) == identifier
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "bearer authentication" in candidate.policy_reason
    assert "client-specific production authorization" in candidate.policy_reason


def test_job_and_application_observations_are_not_complete_boards() -> None:
    job = classify_paylocity_board_url(
        "https://recruiting.paylocity.com/recruiting/jobs/Details/4197092/"
        "Enliven-Inc/Medical-Director"
    )
    application = classify_paylocity_board_url(
        "https://recruiting.paylocity.com/recruiting/jobs/Apply/4306764/"
        "Sionna-Therapeutics-Inc/Director-Regulatory-Affairs-CMC"
    )

    assert job is not None and job.observation_kind == "job_detail"
    assert application is not None and application.observation_kind == "application"
    assert job.board_identifier == ""
    assert application.board_identifier == ""


@pytest.mark.parametrize(
    "url",
    [
        "http://recruiting.paylocity.com/recruiting/jobs/List/1/acme",
        "https://user@recruiting.paylocity.com/recruiting/jobs/List/1/acme",
        "https://recruiting.paylocity.com:8443/recruiting/jobs/List/1/acme",
        "https://recruiting.paylocity.com/recruiting/jobs/List/1/acme#jobs",
        "https://recruiting.paylocity.com.evil.example/recruiting/jobs/List/1/acme",
        "https://jobs.paylocity.com/recruiting/jobs/List/1/acme",
        "https://recruiting.paylocity.com/Recruiting/PrivacyPolicy/List?key=abc",
        "https://recruiting.paylocity.com/recruiting/jobs/List/1/acme?search=engineer",
        "https://recruiting.paylocity.com/recruiting/jobs/All/not-a-guid/acme",
        "https://recruiting.paylocity.com/recruiting/jobs/All/"
        "20c9b648-b2ff-4d0b-a61b-562f1bb835e1/acme/extra",
        "https://recruiting.paylocity.com/recruiting/jobs/Details/not-numeric",
        "https://recruiting.paylocity.com/recruiting/jobs/Details/1/acme",
        "https://recruiting.paylocity.com/recruiting/jobs/Apply/1/acme",
        "https://recruiting.paylocity.com/recruiting/jobs/List/not-numeric/acme",
        "https://recruiting.paylocity.com/recruiting/jobs/List/1/acme/",
        "https://recruiting.paylocity.com/recruiting/jobs/%4cist/1/acme",
    ],
)
def test_classifier_rejects_unsafe_incomplete_and_uninventoried_shapes(url: str) -> None:
    assert classify_paylocity_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_paylocity_policy(
        "https://recruiting.paylocity.com/recruiting/jobs/All/"
        "3af7f13c-9da0-431d-88ee-84db2b3aa074/Angel-Studios"
    )

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


def test_probe_rejects_privacy_policy_url() -> None:
    with pytest.raises(ValueError, match="exact supported-shape"):
        probe_paylocity_policy(
            "https://recruiting.paylocity.com/Recruiting/PrivacyPolicy/List?key=abc"
        )


@pytest.mark.parametrize("kind", ["paylocity", "paylocity_recruiting"])
def test_factory_fails_closed_for_paylocity_aliases(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*bearer authentication"):
        build_connector(kind, "exact-observed-url")
