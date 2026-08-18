from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_ukg_board_url,
    probe_ukg_policy,
)
from fortune_intel.discovery import classify_passive_ats_url

BOARD_ID = "661856a2-40b3-49f9-ab1e-9845cfac508d"
OPPORTUNITY_ID = "0df76cfa-6dce-4ada-a3dd-7125dee884e0"


@pytest.mark.parametrize(
    ("url", "tenant", "board_id", "opportunity_id"),
    [
        (
            "https://recruiting.ultipro.com/SOU1058STHFB",
            "SOU1058STHFB",
            "",
            "",
        ),
        (
            "https://recruiting.ultipro.com/ANN1002/JobBoard/"
            "f76ffcd0-de2d-d6bf-5892-ef0ba64abdba/?q=&o=postedDateDesc",
            "ANN1002",
            "f76ffcd0-de2d-d6bf-5892-ef0ba64abdba",
            "",
        ),
        (
            f"https://recruiting2.ultipro.com/COR1025CVEL/JobBoard/{BOARD_ID}"
            "?q=&o=postedDateDesc&w=&wc=&we=&wpst=&f5=",
            "COR1025CVEL",
            BOARD_ID,
            "",
        ),
        (
            f"https://recruiting2.ultipro.com/WES1033WESTH/JobBoard/{BOARD_ID}/"
            "Opportunity/OpportunityDetail?opportunityId=" + OPPORTUNITY_ID,
            "WES1033WESTH",
            BOARD_ID,
            OPPORTUNITY_ID,
        ),
        (
            "https://recruiting.ultipro.ca/PAY5000PAYCC/JobBoard/"
            "577632a2-3244-45f9-8cc3-e1457962ea52/?q=&o=postedDateDesc",
            "PAY5000PAYCC",
            "577632a2-3244-45f9-8cc3-e1457962ea52",
            "",
        ),
    ],
)
def test_exact_inventoried_ukg_shapes_remain_policy_held(
    url: str,
    tenant: str,
    board_id: str,
    opportunity_id: str,
) -> None:
    candidate = classify_ukg_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.tenant == tenant
    assert candidate.board_id == board_id
    assert candidate.opportunity_id == opportunity_id
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "administrator-issued" in candidate.policy_reason


@pytest.mark.parametrize(
    "url",
    [
        f"http://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}",
        f"https://user@recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}",
        f"https://recruiting.ultipro.com:8443/ACM1000/JobBoard/{BOARD_ID}",
        f"https://recruiting.ultipro.com.evil.example/ACM1000/JobBoard/{BOARD_ID}",
        f"https://jobs.ukg.com/ACM1000/JobBoard/{BOARD_ID}",
        f"https://recruiting.ultipro.com/ACM%2F1000/JobBoard/{BOARD_ID}",
        "https://recruiting.ultipro.com/ACM1000/JobBoard/not-a-uuid",
        f"https://recruiting.ultipro.com/ACM1000/jobs/{BOARD_ID}",
        f"https://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}?unknown=1",
        f"https://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}?q=one&q=two",
        f"https://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}/Opportunity/OpportunityDetail",
        f"https://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}/"
        "Opportunity/OpportunityDetail?opportunityId=not-a-uuid",
        f"https://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}/"
        "Opportunity/OpportunityDetail?opportunityId="
        f"{OPPORTUNITY_ID}&opportunityId={OPPORTUNITY_ID}",
        f"https://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}#jobs",
    ],
)
def test_classifier_rejects_unsafe_unsupported_and_lookalike_urls(url: str) -> None:
    assert classify_ukg_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_ukg_policy(f"https://recruiting2.ultipro.com/ACM1000/JobBoard/{BOARD_ID}")

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


def test_passive_inventory_still_recognizes_exact_ukg_board_url() -> None:
    url = f"https://recruiting.ultipro.com/ACM1000/JobBoard/{BOARD_ID}"

    fingerprint = classify_passive_ats_url(url, origin_page="https://acme.example/careers")

    assert fingerprint is not None
    assert fingerprint.family == "ukg"
    assert fingerprint.observed_url == url


@pytest.mark.parametrize("kind", ["ukg", "ukg-pro-recruiting", "ultipro"])
def test_factory_fails_closed_for_policy_held_ukg(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held"):
        build_connector(kind, "exact-observed-url")
