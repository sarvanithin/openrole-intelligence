from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_paycor_board_url,
    probe_paycor_policy,
)
from fortune_intel.discovery import classify_unknown_external_career_url


@pytest.mark.parametrize(
    ("url", "client_id"),
    [
        (
            "https://recruitingbypaycor.com/career/CareerHome.action?"
            "clientId=8a7883d080d90be90180dd1b6d6300d7",
            "8a7883d080d90be90180dd1b6d6300d7",
        ),
        (
            "https://recruitingbypaycor.com/career/CareerHome.action?"
            "clientId=8a78858b5d7cdee6015d99adbcb96b94",
            "8a78858b5d7cdee6015d99adbcb96b94",
        ),
        (
            "https://recruitingbypaycor.com/career/CareerHome.action?"
            "clientId=8a7883c686c42298018704f3e3711ee7",
            "8a7883c686c42298018704f3e3711ee7",
        ),
        (
            "https://recruitingbypaycor.com/career/CareerHome.action?"
            "clientId=8a7883c67b979504017bd02a03511b78",
            "8a7883c67b979504017bd02a03511b78",
        ),
        (
            "https://recruitingbypaycor.com/career/CareerHome.action?"
            "clientId=8a7883d09959814e019963a847ea0351",
            "8a7883d09959814e019963a847ea0351",
        ),
    ],
)
def test_exact_inventoried_paycor_boards_remain_policy_held(
    url: str,
    client_id: str,
) -> None:
    candidate = classify_paycor_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.host == "recruitingbypaycor.com"
    assert candidate.client_id == client_id
    assert candidate.board_path == "/career/CareerHome.action"
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "client-admin activation" in candidate.policy_reason


@pytest.mark.parametrize(
    "url",
    [
        "http://recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://user@recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://recruitingbypaycor.com:8443/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7#jobs",
        "https://recruitingbypaycor.com.evil.example/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://www.recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://www.paycor.com/career/CareerHome.action?clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://recruitingbypaycor.com/career%2FCareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://recruitingbypaycor.com/career/careerhome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7",
        "https://recruitingbypaycor.com/career/CareerHome.action",
        "https://recruitingbypaycor.com/career/CareerHome.action?clientId=not-hex",
        "https://recruitingbypaycor.com/career/CareerHome.action?"
        "clientid=8a7883d080d90be90180dd1b6d6300d7",
        "https://recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7&lang=en",
        "https://recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7&"
        "clientId=8a78858b5d7cdee6015d99adbcb96b94",
    ],
)
def test_classifier_rejects_unsafe_incomplete_and_lookalike_urls(url: str) -> None:
    assert classify_paycor_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_paycor_policy(
        "https://recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7"
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


def test_unknown_inventory_classification_remains_unchanged() -> None:
    url = (
        "https://recruitingbypaycor.com/career/CareerHome.action?"
        "clientId=8a7883d080d90be90180dd1b6d6300d7"
    )
    fingerprint = classify_unknown_external_career_url(
        url,
        origin_page="https://ensigngroup.example/careers",
    )

    assert fingerprint is not None
    assert fingerprint.family == "unknown_external"
    assert fingerprint.observed_url == url


@pytest.mark.parametrize("kind", ["paycor", "paycor-recruiting", "recruiting-by-paycor"])
def test_factory_fails_closed_for_policy_held_paycor(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*client-admin activation"):
        build_connector(kind, "exact-observed-url")
