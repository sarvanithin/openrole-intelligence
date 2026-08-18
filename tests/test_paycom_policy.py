from __future__ import annotations

import pytest

from fortune_intel.connectors import (
    build_connector,
    classify_paycom_board_url,
    probe_paycom_policy,
)
from fortune_intel.discovery import classify_unknown_external_career_url

CLIENT_KEY = "7FAD41E489F0E4DB4546880755BB9E49"


@pytest.mark.parametrize(
    ("url", "family", "page_kind", "client_key", "job_id"),
    [
        (
            "https://www.paycomonline.net/v4/ats/web.php/jobs?"
            "clientkey=434D8E11B53DE940A5456677337F30F5",
            "legacy_query",
            "board",
            "434D8E11B53DE940A5456677337F30F5",
            "",
        ),
        (
            "https://www.paycomonline.net/v4/ats/web.php/jobs?"
            "clientkey=7FF98BDB027D9F9644B644D93EB039AB&fromClientSide=true",
            "legacy_query",
            "board",
            "7FF98BDB027D9F9644B644D93EB039AB",
            "",
        ),
        (
            "https://www.paycomonline.net/v4/ats/web.php/jobs?"
            "clientkey=7C5AC05D8D2EC046AE4FAF26F5F9712E&"
            "session_nonce=3cec51b71562545878f1e8eadc5d2bf8",
            "legacy_query",
            "board",
            "7C5AC05D8D2EC046AE4FAF26F5F9712E",
            "",
        ),
        (
            "https://www.paycomonline.net/v4/ats/web.php/jobs/ViewJobDetails?"
            "job=67336&clientkey=91989CEA70627F35DBDEA57AC03E0A2B",
            "legacy_query",
            "job_detail",
            "91989CEA70627F35DBDEA57AC03E0A2B",
            "67336",
        ),
        (
            f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page",
            "portal",
            "board",
            CLIENT_KEY,
            "",
        ),
        (
            f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/jobs/10714",
            "portal",
            "job_detail",
            CLIENT_KEY,
            "10714",
        ),
    ],
)
def test_exact_inventoried_paycom_shapes_remain_policy_held(
    url: str,
    family: str,
    page_kind: str,
    client_key: str,
    job_id: str,
) -> None:
    candidate = classify_paycom_board_url(url)

    assert candidate is not None
    assert candidate.observed_url == url
    assert candidate.host == "www.paycomonline.net"
    assert candidate.portal_family == family
    assert candidate.page_kind == page_kind
    assert candidate.client_key == client_key
    assert candidate.job_id == job_id
    assert candidate.activation_allowed is False
    assert candidate.policy_status == "review_required"
    assert "written authorization" in candidate.policy_reason


@pytest.mark.parametrize(
    "url",
    [
        f"http://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page",
        f"https://user@www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page",
        f"https://www.paycomonline.net:8443/v4/ats/web.php/portal/{CLIENT_KEY}/career-page",
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page#jobs",
        f"https://www.paycomonline.net.evil.example/v4/ats/web.php/portal/{CLIENT_KEY}/career-page",
        f"https://jobs.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page",
        f"https://paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page",
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}%2Fextra/career-page",
        "https://www.paycomonline.net/v4/ats/web.php/jobs",
        "https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey=not-hex",
        "https://www.paycomonline.net/v4/ats/web.php/jobs?"
        f"clientkey={CLIENT_KEY}&clientkey={CLIENT_KEY}",
        f"https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={CLIENT_KEY}&unknown=1",
        f"https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={CLIENT_KEY}&"
        "fromClientSide=false",
        f"https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={CLIENT_KEY}&"
        "session_nonce=bad",
        f"https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={CLIENT_KEY}&"
        "fromClientSide=true&session_nonce=3cec51b71562545878f1e8eadc5d2bf8",
        f"https://www.paycomonline.net/v4/ats/web.php/jobs/ViewJobDetails?clientkey={CLIENT_KEY}",
        "https://www.paycomonline.net/v4/ats/web.php/jobs/ViewJobDetails?"
        f"job=abc&clientkey={CLIENT_KEY}",
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page?lang=en",
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/jobs",
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/jobs/abc",
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/jobs/123/",
    ],
)
def test_classifier_rejects_unsafe_incomplete_and_lookalike_urls(url: str) -> None:
    assert classify_paycom_board_url(url) is None


def test_policy_probe_performs_no_network_and_cannot_activate() -> None:
    result = probe_paycom_policy(
        f"https://www.paycomonline.net/v4/ats/web.php/portal/{CLIENT_KEY}/career-page"
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
        "https://www.paycomonline.net/v4/ats/web.php/jobs?"
        "clientkey=434D8E11B53DE940A5456677337F30F5"
    )
    fingerprint = classify_unknown_external_career_url(
        url,
        origin_page="https://auburnbank.example/careers",
    )

    assert fingerprint is not None
    assert fingerprint.family == "unknown_external"
    assert fingerprint.observed_url == url


@pytest.mark.parametrize("kind", ["paycom", "paycom-ats", "paycom-online"])
def test_factory_fails_closed_for_policy_held_paycom(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*written authorization"):
        build_connector(kind, "exact-observed-url")
