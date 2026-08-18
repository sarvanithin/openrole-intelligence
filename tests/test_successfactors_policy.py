from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_successfactors_board_url,
    probe_successfactors_policy,
)


@pytest.mark.parametrize(
    ("url", "family", "company", "kind"),
    [
        (
            "https://career4.successfactors.com/careers?company=amwater",
            "career",
            "amwater",
            "board",
        ),
        (
            "https://career8.successfactors.com/career?career_company=aosmith&lang=en_US&"
            "company=aosmith&company=aosmith&site=",
            "career",
            "aosmith",
            "board",
        ),
        (
            "https://career41.sapsf.com/careers?company=QUANTUMP&site=&lang=en_US&"
            "login_ns=login&loginFlowRequired=true",
            "career",
            "QUANTUMP",
            "board",
        ),
        (
            "https://performancemanager4.successfactors.com/sf/careers/jobsearch?bplte_company=NWL",
            "performance_manager",
            "NWL",
            "board",
        ),
        (
            "https://performancemanager4.successfactors.com/sf/careers?company=freeportmc",
            "performance_manager",
            "freeportmc",
            "board",
        ),
        (
            "https://career2.successfactors.eu/sfcareer/jobreqcareerpvt?jobId=515584&"
            "company=CRH&st=27F41FE817AA0312F102D7282615344207C99A9C",
            "private_job",
            "CRH",
            "job_detail",
        ),
    ],
)
def test_exact_inventoried_successfactors_shapes_remain_policy_held(
    url: str,
    family: str,
    company: str,
    kind: str,
) -> None:
    candidate = classify_successfactors_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.portal_family == family
    assert candidate.company_identifier == company
    assert candidate.observation_kind == kind
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "OAuth credentials" in candidate.policy_reason
    assert "Recruiting export/field permissions" in candidate.policy_reason


def test_private_job_observation_is_not_mistaken_for_complete_board() -> None:
    candidate = classify_successfactors_board_url(
        "https://career2.successfactors.eu/sfcareer/jobreqcareerpvt?jobId=515584&"
        "company=CRH&st=27F41FE817AA0312F102D7282615344207C99A9C"
    )

    assert candidate is not None
    assert candidate.observation_kind == "job_detail"
    assert candidate.job_identifier == "515584"
    assert candidate.activation_allowed is False


@pytest.mark.parametrize(
    "url",
    [
        "http://career4.successfactors.com/careers?company=acme",
        "https://user@career4.successfactors.com/careers?company=acme",
        "https://career4.successfactors.com:8443/careers?company=acme",
        "https://career4.successfactors.com/careers?company=acme#jobs",
        "https://career4.successfactors.com.evil.example/careers?company=acme",
        "https://career99.successfactors.com/careers?company=acme",
        "https://career4.sapsf.com/careers?company=acme",
        "https://www.successfactors.com/careers?company=acme",
        "https://career4.successfactors.com/careers",
        "https://career4.successfactors.com/careers?company=",
        "https://career4.successfactors.com/career?company=acme&company=other",
        "https://career4.successfactors.com/career?career_company=other&company=acme",
        "https://career4.successfactors.com/career?company=acme&lang=en-US",
        "https://career4.successfactors.com/career/?company=acme",
        "https://career4.successfactors.com/%63areer?company=acme",
        "https://performancemanager4.successfactors.com/sf/careers/jobsearch?company=acme",
        "https://career2.successfactors.eu/sfcareer/jobreqcareerpvt?jobId=515584&company=CRH",
        "https://career2.successfactors.eu/sfcareer/jobreqcareerpvt?jobId=bad&company=CRH&"
        "st=27F41FE817AA0312F102D7282615344207C99A9C",
    ],
)
def test_classifier_rejects_unsafe_incomplete_and_uninventoried_shapes(url: str) -> None:
    assert classify_successfactors_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_successfactors_policy(
        "https://performancemanager.successfactors.eu/sf/careers/jobsearch?"
        "bplte_company=C0000161074P"
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


def test_probe_rejects_unobserved_shard() -> None:
    with pytest.raises(ValueError, match="exact supported-shape"):
        probe_successfactors_policy("https://career99.sapsf.com/careers?company=acme")


@pytest.mark.parametrize("kind", ["successfactors", "sap_successfactors", "sapsf"])
def test_factory_fails_closed_for_successfactors_aliases(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*OAuth credentials"):
        build_connector(kind, "exact-observed-url")
