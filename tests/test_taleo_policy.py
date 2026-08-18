from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_taleo_board_url,
    probe_taleo_policy,
)
from fortune_intel.discovery import classify_passive_ats_url


@pytest.mark.parametrize(
    ("url", "family", "zone", "page_kind", "section", "organization", "job_id"),
    [
        (
            "https://aarcorp.taleo.net/careersection/2/jobsearch.ftl?lang=en",
            "enterprise",
            "aarcorp",
            "job_search",
            "2",
            "",
            "",
        ),
        (
            "https://cinfin.taleo.net/careersection/ex/jobsearch.ftl?"
            "f=JOB_TYPE%282%29&ignoreSavedQuery",
            "enterprise",
            "cinfin",
            "job_search",
            "ex",
            "",
            "",
        ),
        (
            "https://epco.taleo.net/careersection/it/jobsearch.ftl?lang=en&"
            "radiusType=M&searchExpanded=false&radius=1&jobfield=4501372523&"
            "jobfield=4401372523",
            "enterprise",
            "epco",
            "job_search",
            "it",
            "",
            "",
        ),
        (
            "https://valero.taleo.net/careersection/2/jobdetail.ftl?job=260010W&"
            "tz=GMT-05%3A00&tzname=America%2FChicago",
            "enterprise",
            "valero",
            "job_detail",
            "2",
            "",
            "260010W",
        ),
        (
            "https://valero.taleo.net/careersection/"
            "vlo-pembroke+career+section/jobsearch.ftl?lang=en",
            "enterprise",
            "valero",
            "job_search",
            "vlo-pembroke+career+section",
            "",
            "",
        ),
        (
            "https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults?"
            "org=HAWKINSCHEMICAL&cws=40",
            "business_edition",
            "phe",
            "search_results",
            "",
            "HAWKINSCHEMICAL",
            "",
        ),
        (
            "https://phg.tbe.taleo.net/phg04/ats/careers/v2/jobSearch?"
            "act=redirectCws&cws=37&org=NEKTAR",
            "business_edition",
            "phg",
            "job_search",
            "",
            "NEKTAR",
            "",
        ),
        (
            "https://phf.tbe.taleo.net/dispatcher/servlet/DispatcherServlet?"
            "org=JSHR6E&act=redirectCwsV2&cws=53",
            "business_edition",
            "phf",
            "redirect",
            "",
            "JSHR6E",
            "",
        ),
    ],
)
def test_exact_inventoried_taleo_shapes_remain_policy_held(
    url: str,
    family: str,
    zone: str,
    page_kind: str,
    section: str,
    organization: str,
    job_id: str,
) -> None:
    candidate = classify_taleo_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.portal_family == family
    assert candidate.zone == zone
    assert candidate.page_kind == page_kind
    assert candidate.section == section
    assert candidate.organization == organization
    assert candidate.job_id == job_id
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "express written permission" in candidate.policy_reason


@pytest.mark.parametrize(
    "url",
    [
        "http://acme.taleo.net/careersection/ex/jobsearch.ftl",
        "https://user@acme.taleo.net/careersection/ex/jobsearch.ftl",
        "https://acme.taleo.net:8443/careersection/ex/jobsearch.ftl",
        "https://acme.taleo.net/careersection/ex/jobsearch.ftl#jobs",
        "https://acme.taleo.net.evil.example/careersection/ex/jobsearch.ftl",
        "https://foo.bar.taleo.net/careersection/ex/jobsearch.ftl",
        "https://client.taleo.net/careersection/ex/jobsearch.ftl",
        "https://acme.taleo.com/careersection/ex/jobsearch.ftl",
        "https://acme.taleo.net/careersection/ex%2Finternal/jobsearch.ftl",
        "https://acme.taleo.net/careersection/../jobsearch.ftl",
        "https://uhg.taleo.net/careersection/10000/mysubmissions.ftl?lang=en",
        "https://unifirst.taleo.net/careersection/iam/accessmanagement/login.jsf",
        "https://acme.taleo.net/careersection/ex/jobsearch.ftl?unknown=1",
        "https://acme.taleo.net/careersection/ex/jobsearch.ftl?lang=en&lang=fr",
        "https://acme.taleo.net/careersection/ex/jobdetail.ftl",
        "https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=bad%2Fid",
        "https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=1&job=2",
        "https://phe.tbe.taleo.net/phh01/ats/careers/v2/jobSearch?org=ACME&cws=1",
        "https://phe.tbe.taleo.net/phe01/ats/careers/v2/jobSearch?cws=1",
        "https://phe.tbe.taleo.net/phe01/ats/careers/v2/jobSearch?org=ACME",
        "https://phe.tbe.taleo.net/phe01/ats/careers/v2/jobSearch?org=ACME&org=OTHER&cws=1",
        "https://phe.tbe.taleo.net/phe01/ats/careers/v2/jobSearch?org=ACME&cws=1&act=unknown",
        "https://phe.tbe.taleo.net/phe01/ats/careers/v2/jobSearch?org=ACME&cws=1&"
        "act=redirectCws&act=redirectCwsV2",
        "https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults?org=ACME&cws=1&"
        "act=redirectCwsV2",
        "https://phh.tbe.taleo.net/phh04/ats/careers/v2/candidateLogin?org=KHOV&cws=46",
        "https://phf.tbe.taleo.net/dispatcher/servlet/DispatcherServlet?org=JSHR6E&cws=53",
    ],
)
def test_classifier_rejects_unsafe_non_job_and_lookalike_urls(url: str) -> None:
    assert classify_taleo_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_taleo_policy(
        "https://evergy.taleo.net/careersection/evergy_external_career_section/"
        "jobsearch.ftl?lang=en"
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


def test_passive_inventory_still_recognizes_exact_taleo_board_url() -> None:
    url = "https://aarcorp.taleo.net/careersection/2/jobsearch.ftl?lang=en"
    fingerprint = classify_passive_ats_url(url, origin_page="https://aarcorp.example/careers")

    assert fingerprint is not None
    assert fingerprint.family == "taleo"
    assert fingerprint.observed_url == url


@pytest.mark.parametrize(
    "kind",
    ["taleo", "oracle-taleo", "taleo-enterprise", "taleo-business-edition", "tbe"],
)
def test_factory_fails_closed_for_policy_held_taleo(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*express written permission"):
        build_connector(kind, "exact-observed-url")
