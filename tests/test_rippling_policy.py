from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_rippling_board_url,
    probe_rippling_policy,
)


@pytest.mark.parametrize(
    ("url", "family", "company", "kind"),
    [
        (
            "https://ats.rippling.com/d-wave-quantum/jobs",
            "company_board",
            "d-wave-quantum",
            "board",
        ),
        (
            "https://ats.rippling.com/stem-inc/jobs",
            "company_board",
            "stem-inc",
            "board",
        ),
        (
            "https://ats.rippling.com/opendoor/jobs/39a8088f-6534-4cdb-8257-1e39de641093/apply",
            "company_board",
            "opendoor",
            "application",
        ),
        (
            "https://ats.rippling.com/en-AU/ataibeckley/jobs/"
            "f3884e5d-1d3e-40ca-87c7-2c7090d3c3c5?"
            "st=432c464f-6fe1-4f74-9144-2ad460f0dc06",
            "localized_job",
            "ataibeckley",
            "job_detail",
        ),
    ],
)
def test_exact_inventoried_rippling_shapes_remain_policy_held(
    url: str,
    family: str,
    company: str,
    kind: str,
) -> None:
    candidate = classify_rippling_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.portal_family == family
    assert candidate.company_slug == company
    assert candidate.observation_kind == kind
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "company-bound API key or OAuth token" in candidate.policy_reason
    assert "administrator-authorized scopes" in candidate.policy_reason


def test_job_and_application_observations_are_not_complete_boards() -> None:
    job = classify_rippling_board_url(
        "https://ats.rippling.com/en-AU/ataibeckley/jobs/"
        "f3884e5d-1d3e-40ca-87c7-2c7090d3c3c5?"
        "st=432c464f-6fe1-4f74-9144-2ad460f0dc06"
    )
    application = classify_rippling_board_url(
        "https://ats.rippling.com/opendoor/jobs/39a8088f-6534-4cdb-8257-1e39de641093/apply"
    )

    assert job is not None and job.observation_kind == "job_detail"
    assert application is not None and application.observation_kind == "application"
    assert job.activation_allowed is False
    assert application.activation_allowed is False


@pytest.mark.parametrize(
    "url",
    [
        "http://ats.rippling.com/acme/jobs",
        "https://user@ats.rippling.com/acme/jobs",
        "https://ats.rippling.com:8443/acme/jobs",
        "https://ats.rippling.com/acme/jobs#openings",
        "https://ats.rippling.com.evil.example/acme/jobs",
        "https://www.rippling.com/acme/jobs",
        "https://ats.rippling.com/Acme/jobs",
        "https://ats.rippling.com/acme/jobs/",
        "https://ats.rippling.com/acme/jobs?search=engineer",
        "https://ats.rippling.com/acme/%6aobs",
        "https://ats.rippling.com/acme//jobs",
        "https://ats.rippling.com/acme/jobs/not-a-guid/apply",
        "https://ats.rippling.com/acme/jobs/39a8088f-6534-4cdb-8257-1e39de641093",
        "https://ats.rippling.com/en-US/acme/jobs/"
        "f3884e5d-1d3e-40ca-87c7-2c7090d3c3c5?"
        "st=432c464f-6fe1-4f74-9144-2ad460f0dc06",
        "https://ats.rippling.com/en-AU/acme/jobs/f3884e5d-1d3e-40ca-87c7-2c7090d3c3c5",
        "https://ats.rippling.com/en-AU/acme/jobs/f3884e5d-1d3e-40ca-87c7-2c7090d3c3c5?st=bad",
        "https://ats.rippling.com/en-AU/acme/jobs/"
        "f3884e5d-1d3e-40ca-87c7-2c7090d3c3c5?"
        "st=432c464f-6fe1-4f74-9144-2ad460f0dc06&other=1",
    ],
)
def test_classifier_rejects_unsafe_incomplete_and_uninventoried_shapes(url: str) -> None:
    assert classify_rippling_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_rippling_policy("https://ats.rippling.com/zymeworks-careers/jobs")

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


def test_probe_rejects_guessed_localized_board() -> None:
    with pytest.raises(ValueError, match="exact supported-shape"):
        probe_rippling_policy("https://ats.rippling.com/en-US/acme/jobs")


@pytest.mark.parametrize("kind", ["rippling", "rippling_ats"])
def test_factory_fails_closed_for_rippling_aliases(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*company-bound"):
        build_connector(kind, "exact-observed-url")
