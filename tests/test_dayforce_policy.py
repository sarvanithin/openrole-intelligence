from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_dayforce_board_url,
    probe_dayforce_policy,
)


@pytest.mark.parametrize(
    ("url", "family", "tenant", "portal", "kind"),
    [
        (
            "https://jobs.dayforcehcm.com/en-US/MUELLERINDUSTRIES/CANDIDATEPORTAL",
            "jobs_portal",
            "MUELLERINDUSTRIES",
            "CANDIDATEPORTAL",
            "board",
        ),
        (
            "https://jobs.dayforcehcm.com/en-US/angio/AngioCareers/jobs/4257",
            "jobs_portal",
            "angio",
            "AngioCareers",
            "job_detail",
        ),
        (
            "https://jobs.dayforcehcm.com/hillman/HillmanUSJB?searchText=finance",
            "jobs_portal",
            "hillman",
            "HillmanUSJB",
            "board",
        ),
        (
            "https://us231.dayforcehcm.com/CandidatePortal/en-US/bluelinx/SITE/"
            "CANDIDATEPORTAL?q=CDL",
            "candidate_portal",
            "bluelinx",
            "CANDIDATEPORTAL",
            "board",
        ),
        (
            "https://dayforcehcm.com/CandidatePortal/en-US/altg/",
            "candidate_portal",
            "altg",
            "",
            "board",
        ),
        (
            "https://www.dayforcehcm.com/CandidatePortal/en-US/loco?d=521",
            "candidate_portal",
            "loco",
            "",
            "board",
        ),
    ],
)
def test_exact_inventoried_dayforce_shapes_remain_policy_held(
    url: str,
    family: str,
    tenant: str,
    portal: str,
    kind: str,
) -> None:
    candidate = classify_dayforce_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.portal_family == family
    assert candidate.tenant_identifier == tenant
    assert candidate.portal_identifier == portal
    assert candidate.observation_kind == kind
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "explicit, verifiable client consent" in candidate.policy_reason


def test_exact_job_detail_is_not_mistaken_for_complete_board() -> None:
    candidate = classify_dayforce_board_url(
        "https://jobs.dayforcehcm.com/en-US/angio/AngioCareers/jobs/4280"
    )

    assert candidate is not None
    assert candidate.observation_kind == "job_detail"
    assert candidate.job_identifier == "4280"
    assert candidate.activation_allowed is False


@pytest.mark.parametrize(
    "url",
    [
        "http://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL",
        "https://user@jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL",
        "https://jobs.dayforcehcm.com:8443/en-US/acme/CANDIDATEPORTAL",
        "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL#jobs",
        "https://jobs.dayforcehcm.com.evil.example/en-US/acme/CANDIDATEPORTAL",
        "https://us999.dayforcehcm.com/CandidatePortal/en-US/acme",
        "https://usr57.dayforcehcm.com/MyDayforce/MyDayforce.aspx",
        "https://www.dayforcehcm.com/MyDayforce/MyDayforce.aspx",
        "https://jobs.dayforcehcm.com/en-CA/acme/CANDIDATEPORTAL",
        "https://jobs.dayforcehcm.com/en-US/acme",
        "https://jobs.dayforcehcm.com/en-US/acme/jobs/1",
        "https://jobs.dayforcehcm.com/en-US/acme/portal/jobs/not-numeric",
        "https://jobs.dayforcehcm.com/en-US/acme/portal/jobs/1/extra",
        "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/",
        "https://jobs.dayforcehcm.com/en-US/acme/%43ANDIDATEPORTAL",
        "https://www.dayforcehcm.com/CandidatePortal/en-US/acme//SITE/portal",
        "https://www.dayforcehcm.com/CandidatePortal/en-US/acme/unknown/portal",
    ],
)
def test_classifier_rejects_unsafe_incomplete_and_uninventoried_shapes(url: str) -> None:
    assert classify_dayforce_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_dayforce_policy(
        "https://us242.dayforcehcm.com/CandidatePortal/en-US/legence/site/studentportal"
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


def test_probe_rejects_employee_login_url() -> None:
    with pytest.raises(ValueError, match="exact supported-shape"):
        probe_dayforce_policy("https://usr57.dayforcehcm.com/MyDayforce/MyDayforce.aspx")


@pytest.mark.parametrize("kind", ["dayforce", "dayforce_hcm", "ceridian"])
def test_factory_fails_closed_for_dayforce_aliases(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*explicit, verifiable client consent"):
        build_connector(kind, "exact-observed-url")
