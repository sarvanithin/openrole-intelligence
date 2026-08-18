from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_eightfold_board_url,
    probe_eightfold_policy,
)
from fortune_intel.discovery import classify_passive_ats_url


@pytest.mark.parametrize(
    "url",
    [
        "https://lamresearch.eightfold.ai/careers?domain=lamresearch.com&start=0",
        "https://ngc.eightfold.ai/careerhub",
        "https://corteva.eightfold.ai/careers/job/893396844488?domain=corteva.com",
    ],
)
def test_exact_inventoried_board_shapes_remain_policy_held(url: str) -> None:
    candidate = classify_eightfold_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "Position:READ" in candidate.policy_reason


@pytest.mark.parametrize(
    "url",
    [
        "http://acme.eightfold.ai/careers",
        "https://user@acme.eightfold.ai/careers",
        "https://api.eightfold.ai/careers",
        "https://acme.eightfold.ai/events/open?domain=acme.com",
        "https://acme.eightfold.ai/careers/unknown",
        "https://acme.eightfold.ai/careers?domain=https://acme.com",
        "https://fakeeightfold.ai/careers",
    ],
)
def test_classifier_rejects_events_unsafe_and_lookalike_urls(url: str) -> None:
    assert classify_eightfold_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_eightfold_policy("https://acme.eightfold.ai/careers?domain=acme.com")

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


def test_passive_inventory_rejects_non_job_eightfold_event() -> None:
    assert (
        classify_passive_ats_url(
            "https://app.eightfold.ai/events/open?domain=acme.com",
            origin_page="https://acme.com/careers",
        )
        is None
    )


def test_factory_fails_closed_for_policy_held_eightfold() -> None:
    with pytest.raises(ValueError, match="policy-held"):
        build_connector("eightfold", "exact-observed-url")
