from __future__ import annotations

import pytest

from fortune_intel.connectors.avature import (
    AVATURE_POLICY_REASON,
    classify_avature_board_url,
    probe_avature_policy,
)
from fortune_intel.connectors.factory import build_connector


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://ally.avature.net/careers", "board"),
        (
            "https://ally.avature.net/careers/SearchJobs/?667=%5B1318373%5D&"
            "667_format=613&listFilterMode=1&jobRecordsPerPage=6&",
            "filtered_search",
        ),
        (
            "https://ally.avature.net/careers/SearchJobs/?667=%5B20865%2C20877%5D&"
            "667_format=613&listFilterMode=1&jobRecordsPerPage=6&",
            "filtered_search",
        ),
        (
            "https://ally.avature.net/careers/SearchJobs/?667="
            "%5B20867%2C20887%2C20871%5D&667_format=613&listFilterMode=1&"
            "jobRecordsPerPage=6&",
            "filtered_search",
        ),
        (
            "https://ally.avature.net/careers/SearchJobs/?667=%5B20870%2C20872%2C"
            "1316147%2C20874%2C20875%2C20885%2C20886%2C20887%5D&667_format=613&"
            "listFilterMode=1&jobRecordsPerPage=6&",
            "filtered_search",
        ),
        (
            "https://ally.avature.net/careers/SearchJobs/?667=%5B20873%2C2057593%5D&"
            "667_format=613&listFilterMode=1&jobRecordsPerPage=6&",
            "filtered_search",
        ),
        (
            "https://ally.avature.net/careers/SearchJobs/?667="
            "%5B20884%2C20879%2C20881%5D&667_format=613&listFilterMode=1&"
            "jobRecordsPerPage=6&",
            "filtered_search",
        ),
        (
            "https://ally.avature.net/careers/SearchJobs/?667=%5B265477%5D&"
            "667_format=613&listFilterMode=1&jobRecordsPerPage=6&",
            "filtered_search",
        ),
        (
            "https://ally.avature.net/careers/SearchJobs/%23dfs?"
            "listFilterMode=1&jobRecordsPerPage=6&",
            "filtered_search",
        ),
    ],
)
def test_exact_inventoried_avature_board_shapes_are_policy_held(url: str, kind: str) -> None:
    candidate = classify_avature_board_url(url)

    assert candidate is not None
    assert candidate.host == "ally.avature.net"
    assert candidate.tenant == "ally"
    assert candidate.observation_kind == kind
    assert candidate.activation_allowed is False
    assert candidate.policy_reason == AVATURE_POLICY_REASON


@pytest.mark.parametrize(
    "url",
    [
        "https://ally.avature.net/talentcommunity",
        "https://jackhenry.avature.net/careers/Login",
        "https://jackhenry.avature.net/internships",
        "https://jackhenry.avature.net/talentNetwork",
        "https://ross.avature.net/talentcommunity",
        "https://synopsys.avature.net/talentcommunity?jobId=88&source=Radancy",
        "http://ally.avature.net/careers",
        "https://user@ally.avature.net/careers",
        "https://ally.avature.net:8443/careers",
        "https://ally.avature.net/careers#jobs",
        "https://acme.avature.net/careers",
        "https://ally.avature.net.evil.example/careers",
        "https://ally.avature.net/careers/",
        "https://ally.avature.net/careers/jobs",
        "https://ally.avature.net/careers/%53earchJobs/",
        "https://ally.avature.net/careers//SearchJobs/",
        "https://ally.avature.net/careers/SearchJobs/",
        "https://ally.avature.net/careers/SearchJobs/?listFilterMode=1&jobRecordsPerPage=6",
        "https://ally.avature.net/careers/SearchJobs/?667=%5B999%5D&667_format=613&"
        "listFilterMode=1&jobRecordsPerPage=6",
        "https://ally.avature.net/careers/SearchJobs/?667=%5B1318373%5D&"
        "667_format=613&listFilterMode=1&listFilterMode=1&jobRecordsPerPage=6",
        "https://ally.avature.net/careers/SearchJobs/%23dfs?listFilterMode=1&jobRecordsPerPage=100",
    ],
)
def test_unobserved_or_non_manifest_avature_urls_are_rejected(url: str) -> None:
    assert classify_avature_board_url(url) is None


def test_probe_is_zero_network_and_cannot_activate() -> None:
    result = probe_avature_policy("https://ally.avature.net/careers")

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


def test_probe_rejects_talent_community() -> None:
    with pytest.raises(ValueError, match="exact supported-shape"):
        probe_avature_policy("https://ross.avature.net/talentcommunity")


@pytest.mark.parametrize("kind", ["avature", "avature_ats"])
def test_factory_fails_closed_for_avature_aliases(kind: str) -> None:
    with pytest.raises(ValueError, match="policy-held.*custom endpoints.*credentials/API keys"):
        build_connector(kind, "ally")
