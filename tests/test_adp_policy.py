from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_adp_board_url,
    probe_adp_policy,
)


@pytest.mark.parametrize(
    ("url", "family", "tenant"),
    [
        (
            "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
            "recruitment.html?cid=7d6e4e14-797d-4141-abf5-91d0fc79dbe8&"
            "ccId=19000101_000001&lang=en_US",
            "workforce_now",
            "7d6e4e14-797d-4141-abf5-91d0fc79dbe8",
        ),
        (
            "https://workforcenow.adp.com/jobs/apply/posting.html?client=unitil&"
            "ccId=19000101_000001&type=JS&lang=en_US",
            "workforce_now",
            "unitil",
        ),
        (
            "https://recruiting.adp.com/srccar/public/RTI.home?c=1213701&d=External",
            "recruiting_management",
            "1213701",
        ),
        (
            "https://myjobs.adp.com/kaisercareers/cx/job-listing",
            "myjobs",
            "kaisercareers",
        ),
        (
            "https://myjobs.adp.com/albanyjobs/cx?__tx_annotation=false&"
            "c=1213701&d=External&sor=adprm",
            "myjobs",
            "albanyjobs",
        ),
    ],
)
def test_exact_inventoried_adp_board_shapes_remain_policy_held(
    url: str,
    family: str,
    tenant: str,
) -> None:
    candidate = classify_adp_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.portal_family == family
    assert candidate.tenant_identifier == tenant
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "Consumer Application Registry" in candidate.policy_reason
    assert "Practitioner" in candidate.policy_reason


@pytest.mark.parametrize(
    "url",
    [
        "http://myjobs.adp.com/acme",
        "https://user@myjobs.adp.com/acme",
        "https://myjobs.adp.com:8443/acme",
        "https://myjobs.adp.com/acme#jobs",
        "https://myjobs.adp.ca/acme",
        "https://myjobs.adp.com.evil.example/acme",
        "https://www.adp.com/careers",
        "https://myjobs.adp.com/apply/auth?lang=us-US",
        "https://myjobs.adp.com/acme/cx/job-details/123",
        "https://myjobs.adp.com/acme//cx",
        "https://myjobs.adp.com/acme/cx/",
        "https://myjobs.adp.com/acme/%63x/job-listing",
        "https://recruiting.adp.com/srccar/public/RTI.home?c=1160751&d",
        "https://recruiting.adp.com/srccar/public/RTI.home?c=abc&d=External",
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?"
        "cid=7d6e4e14-797d-4141-abf5-91d0fc79dbe8",
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?"
        "cid=not-a-guid&ccId=19000101_000001",
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?"
        "cid=7d6e4e14-797d-4141-abf5-91d0fc79dbe8&"
        "cid=02835ad7-1b2e-4eb2-9773-3454d03b1a3e&ccId=19000101_000001",
    ],
)
def test_classifier_rejects_unsafe_incomplete_and_lookalike_urls(url: str) -> None:
    assert classify_adp_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_adp_policy("https://myjobs.adp.com/brcoffeejobs/cx")

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


def test_probe_rejects_non_board_url() -> None:
    with pytest.raises(ValueError, match="exact supported-shape"):
        probe_adp_policy("https://myjobs.adp.com/apply/auth?lang=us-US")


def test_factory_fails_closed_for_policy_held_adp() -> None:
    with pytest.raises(ValueError, match="policy-held.*Consumer Application Registry"):
        build_connector("adp", "exact-observed-url")
